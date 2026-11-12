"""Conservative local-edit path for reliably parsed H3 input (experimental)."""
import json
import re
from .backend import BackendError
from .requirements import RequirementsReviewFailed
from .structured_input import parse
from .prose import correction_spec, load_prompt

SCHEMA={"type":"object","properties":{"edits":{"type":"array","maxItems":8,"items":{"type":"object","properties":{k:{"type":"string"} for k in ("before","after","reason")},"required":["before","after","reason"],"additionalProperties":False}},"unresolved":{"type":"array","items":{"type":"string"}}},"required":["edits","unresolved"],"additionalProperties":False}

def protect(text):
    values={}
    def hide(m):
        key="{{PROTECTED_SPEECH_%d}}" % (len(values)+1)
        values[key]=m.group();return key
    return re.sub(r"<d>.*?</d>",hide,text,flags=re.S),values

def apply_edits(text, edits):
    if not isinstance(edits,list) or len(edits)>8:raise BackendError("Invalid local edit list")
    spans=[]
    for e in edits:
        if not isinstance(e,dict) or set(e)!={"before","after","reason"} or not all(isinstance(e[k],str) and e[k].strip() for k in e):raise BackendError("Invalid local edit")
        old,new=e['before'],e['after']
        if old==new or text.count(old)!=1:raise BackendError("Local edit must identify one exact changed span")
        if re.search(r"PROTECTED_SPEECH|<d>|</d>|\[Shot|<Subject|<Picture|<Video|<Audio",old+new):raise BackendError("Local edits cannot modify protected structure or bindings")
        start=text.index(old);end=start+len(old)
        if any(start<b and end>a for a,b,_ in spans):raise BackendError("Overlapping local edits")
        spans.append((start,end,new))
    if sum(b-a for a,b,_ in spans)>max(120,len(text)*.25):raise BackendError("Local edit budget exceeded; refusing full rewrite")
    out=text
    for a,b,new in sorted(spans,reverse=True):out=out[:a]+new+out[b:]
    before,after=parse(text),parse(out)
    if not after.reliable or [(x['number'],x['start_ms']) for x in before.shots]!=[(x['number'],x['start_ms']) for x in after.shots]:raise BackendError("Local edit changed document structure")
    for name in ['overall_soundscape','non_diegetic_music','subject_definitions','summary','retention_analysis']:
        if before.sections.get(name)!=after.sections.get(name):raise BackendError("Local edit changed a protected section")
    return out

def run(backend, brief, original, verify, target, sections, seed=None):
    original_masked,speech=protect(original)
    current=original_masked
    record={'route':'professional_local_repair','version':'professional-v2','rounds':[], 'passed':False}
    def restore(text):
        for token,value in speech.items():text=text.replace(token,value)
        return text
    try:
        for round_no in range(3):
            fx,findings,n=verify(restore(current))
            errors=[f for f in findings if f.severity=='ERROR']
            data={'original':original_masked,'candidate':current,'constraints':brief.constraints,
                  'errors':[{'rule':f.rule,'message':f.msg} for f in errors], 'previous_reviews':record['rounds'],
                  'render_contract':{'nominal_seconds':target.nominal_seconds,'effective_seconds':target.effective_seconds,'tail_marker':target.s_ss()}}
            # Findings can quote speech: protect them as well before sending.
            payload=json.dumps(data,ensure_ascii=False)
            for value in speech.values():
                inner=re.sub(r'^<d>\s*\[[^]]+\]\s*|</d>$','',value)
                if inner:payload=payload.replace(inner,'[protected spoken text]')
            messages=[{'role':'system','content':load_prompt('professional_review.v2.txt')+'\n'+correction_spec(sections,current)}, {'role':'user','content':payload}]
            for attempt in range(2):
                result=backend.json_call(messages,SCHEMA,required=('edits','unresolved'),thinking=False,seed=seed,max_tokens=4000,retries=1,guided=True)
                try:
                    if not isinstance(result.get('unresolved'),list) or not all(isinstance(x,str) for x in result['unresolved']):raise BackendError('Invalid unresolved findings')
                    edited=apply_edits(current,result.get('edits'))
                    break
                except BackendError as invalid:
                    record.setdefault('invalid_proposals',[]).append({'response':result,'error':str(invalid)})
                    if attempt:raise
                    messages += [{'role':'assistant','content':json.dumps(result,ensure_ascii=False)},
                                 {'role':'user','content':'Invalid local edit proposal: '+str(invalid)+'. Return the complete review object with edits [{before, after, reason}] and unresolved [strings]. Use exact unique spans, do not rewrite the document. Every edit needs a nonempty reason.'}]
            record['rounds'].append(result)
            if result['unresolved']:raise BackendError('Professional review unresolved: '+ '; '.join(result['unresolved']))
            if not result['edits']:
                if errors:raise BackendError('Professional input still violates mechanical checks: '+','.join(f.rule for f in errors))
                record['passed']=True;record['changed']=current!=original_masked
                return fx,findings,n,record
            if round_no==2:raise BackendError('Professional correction budget exhausted')
            current=edited
        raise BackendError('Professional review did not finish')
    except BackendError as e:
        raise RequirementsReviewFailed(str(e),record,current) from e
