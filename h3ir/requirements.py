"""Evidence-backed intent extraction and a separate audit against the original.

Opt-in during validation. Model judgements are recorded as such, never advertised
as proof; only literal quotes, schema and typed controls are program-checked.
"""
import json
import re
from dataclasses import replace
from pathlib import Path
from .backend import BackendError, user_message
from .models import Finding
from .language import protected_intent

VERSION = "requirements-v2"
EXTRACT_SCHEMA = {"type":"object", "properties":{
    "requirements":{"type":"array","maxItems":32,"items":{"type":"object","properties":{
        "requirement":{"type":"string"},"source_quote":{"type":"string"}},
        "required":["requirement","source_quote"],"additionalProperties":False}},
    "music":{"type":"object","properties":{"policy":{"type":"string","enum":["unspecified","forbidden","specified"]},"source_quote":{"type":"string"}},"required":["policy","source_quote"],"additionalProperties":False},
    "style":{"type":"object","properties":{"description":{"type":"string"},"source_quote":{"type":"string"}},"required":["description","source_quote"],"additionalProperties":False}},
    "required":["requirements","music","style"],"additionalProperties":False}
REVIEW_SCHEMA = {"type":"object","properties":{"issues":{"type":"array","maxItems":24,"items":{
    "type":"object","properties":{"kind":{"type":"string","enum":["omission","contradiction","unsupported_change","visual_mismatch","uncertain"]},
    "original_quote":{"type":"string"},"candidate_quote":{"type":"string"},"explanation":{"type":"string"}},
    "required":["kind","original_quote","candidate_quote","explanation"],"additionalProperties":False}}},"required":["issues"],"additionalProperties":False}

def _load(name):
    return (Path(__file__).parent/'prompts'/name).read_text(encoding='utf-8')

def _quote(value, original, required=True):
    if not isinstance(value,str) or len(value)>6000 or (required and not value.strip()):
        raise BackendError("Requirement evidence must be a bounded nonempty source quote")
    if value and value not in original:
        raise BackendError(f"Requirement evidence is not present verbatim in the supplied source: {value!r}")
    return value

class RequirementsReviewFailed(BackendError):
    def __init__(self, message, record, candidate):
        super().__init__(message)
        self.record = record
        self.candidate = candidate


def _checked_call(backend, messages, schema, required, check, seed, max_tokens, on_call=None):
    # One explicit repair of invalid JSON/evidence, not an unbounded retry ladder.
    for attempt in range(2):
        if on_call is not None: on_call()
        obj = backend.json_call(messages, schema, required=required, seed=seed,
                                thinking=False, max_tokens=max_tokens,retries=0)
        try:
            result = check(obj)
            result['call_count'] = attempt+1
            return result
        except BackendError as error:
            if attempt:
                raise BackendError(str(error)+"; invalid structured evidence: "+json.dumps(obj,ensure_ascii=False)[:6000]) from error
            messages = [*messages, {'role':'assistant','content':json.dumps(obj,ensure_ascii=False)},
                        {'role':'user','content':'Correct this invalid evidence/schema: '+str(error)+
                         '. Re-read the original. Copy exact contiguous source text, in its original language. '
                         'Do not translate or paraphrase evidence. Omit unsupported invented claims. Return the complete corrected JSON.'}]


def source_text(brief):
    text=protected_intent(brief)
    if brief.constraints:
        extra=protected_intent(replace(brief,intent="\n".join(brief.constraints)))
        text += "\nExplicit caller constraints:\n" + extra
    return text


def extract(backend, brief, seed=None, on_call=None):
    original=source_text(brief)
    messages=[{'role':'system','content':_load('requirements_extract.v1.txt')},
              {'role':'user','content':json.dumps({'original':original,'constraints':brief.constraints,'creativity':brief.creativity},ensure_ascii=False)}]
    return _checked_call(backend,messages,EXTRACT_SCHEMA,('requirements','music','style'),
                         lambda obj:_validate_extraction(obj,original),seed,5000,on_call)


def _validate_extraction(obj, original):
    reqs=obj.get('requirements');music=obj.get('music');style=obj.get('style')
    if not isinstance(reqs,list) or not 1<=len(reqs)<=32 or not isinstance(music,dict) or not isinstance(style,dict):
        raise BackendError("Invalid requirement extraction schema")
    clean=[]
    for item in reqs:
        if not isinstance(item,dict) or set(item)!={'requirement','source_quote'}:
            raise BackendError("Invalid requirement item")
        fact=item['requirement']
        if not isinstance(fact,str) or not fact.strip() or len(fact)>3000:
            raise BackendError("Invalid requirement description")
        clean.append({'requirement':fact,'source_quote':_quote(item['source_quote'],original)})
    if music.get('policy') not in ('unspecified','forbidden','specified'):
        raise BackendError("Invalid music policy")
    _quote(music.get('source_quote'),original,music['policy']!='unspecified')
    if not isinstance(style.get('description'),str) or len(style['description'])>500:
        raise BackendError("Invalid style requirement")
    _quote(style.get('source_quote'),original,bool(style['description']))
    # Reliable structured evidence is not outvoted by a model extraction.
    from .structured_input import parse
    parsed=parse(original)
    if parsed.reliable and parsed.sections.get('non_diegetic_music')=='N/A':
        music={'policy':'forbidden','source_quote':'N/A'}
    return {'version':VERSION,'requirements':clean,'music':music,'style':style,
            'evidence_check':'literal membership only; semantic interpretation remains model judgement'}

def apply(brief, ledger):
    return replace(brief,silent=brief.silent or ledger['music']['policy']=='forbidden',requirements_context=ledger)

def mask_candidate(text, brief):
    # Full and punctuation-split utterances have the same identities on both sides.
    for i,line in enumerate(brief.dialogue,1):
        text=text.replace(line.text,'{{D%d}}'%i)
        for j,part in enumerate(re.split(r'(?<=[,，;；:：.!?。！？])',line.text),1):
            if part.strip():text=text.replace(part,'{{D%d_PART%d}}'%(i,j))
    def opaque(m):
        inner=m[1]
        if re.fullmatch(r'\s*\[[^]]+\]\s*\{\{D\d+(?:_PART\d+)?\}\}\s*',inner):return m.group()
        return '<d>[protected] {{UNMATCHED_SPEECH}}</d>'
    return re.sub(r'<d>(.*?)</d>',opaque,text,flags=re.S)

def review(backend, brief, candidate, seed=None, on_call=None, target=None):
    original=source_text(brief);protected=mask_candidate(candidate,brief)
    if target is None:
        from .grid import Target
        target=Target.build(brief.seconds,brief.aspect,canvas=brief.canvas,megapixels=brief.megapixels)
    render_contract={'nominal_seconds':target.nominal_seconds,'frames':target.frames,'fps':target.fps,
                     'effective_seconds':target.effective_seconds,'tail_marker':target.s_ss(),
                     'rule':'H3 snaps requested duration to the supported frame grid. The computed effective duration and tail marker are valid; do not flag this rounding as a semantic contradiction.'}
    data={'original':original,'requirements':brief.requirements_context,'candidate':protected,
          'render_contract':render_contract,
          'constraints':brief.constraints,'creativity':brief.creativity,
          'assets':[{'index':i+1,'kind':a.kind.value,'role':a.role.value} for i,a in enumerate(brief.assets)]}
    # Only source images, never imagined audio characteristics. No image generation here.
    images=[a.path for a in brief.assets if a.kind.value=='image' and a.path] or None
    messages=[{'role':'system','content':_load('requirements_review.v2.txt')},
              user_message(json.dumps(data,ensure_ascii=False),images)]
    return _checked_call(backend,messages,REVIEW_SCHEMA,('issues',),
                         lambda obj:_validate_review(obj,original,protected,len(images or [])),seed,4500,on_call)


def _validate_review(obj, original, protected, image_count):
    issues=obj.get('issues')
    if not isinstance(issues,list) or len(issues)>24:raise BackendError('Invalid semantic audit schema')
    clean=[]
    for x in issues:
        if not isinstance(x,dict) or set(x)!={'kind','original_quote','candidate_quote','explanation'} or x['kind'] not in ('omission','contradiction','unsupported_change','visual_mismatch','uncertain'):
            raise BackendError('Invalid semantic audit issue')
        _quote(x['original_quote'],original)
        _quote(x['candidate_quote'],protected,x['kind']!='omission')
        if not isinstance(x['explanation'],str) or not 0<len(x['explanation'])<=3000:raise BackendError('Invalid semantic explanation')
        clean.append(x)
    return {'issues':clean,'checked_against_original':True,'image_count':image_count,
            'judgement':'model audit; not a formal proof'}

def findings(audit):
    result=[]
    for issue in audit['issues']:
        fix=dict(issue)
        if issue['kind']=='visual_mismatch':
            # A second VLM guess is not ground truth. Keep the original audit for
            # evidence, but do not order the writer to replace one guess with another.
            fix['explanation'] = (
                'This candidate-specific visual assertion is not reliably supported. '
                'Do not substitute a new guessed pose, gesture name, identity or object count. '
                'If the original text explicitly specifies this detail, retain that requirement; '
                'do not override it with a visual guess. Otherwise remove the unsupported specifics '
                'and describe continuous movement into the exact reference-defined state. '
                'Keep every original requirement, object and reference binding. '
                'The attached source image remains the authoritative visual anchor.')
        result.append(Finding('DINGO-requirement-preservation','ERROR',json.dumps(fix,ensure_ascii=False)))
    return result

def mechanical(brief,candidate):
    if brief.silent:
        from .validate import _sections
        sections,_=_sections(candidate,('non_diegetic_music',))
        if sections.get('non_diegetic_music','').strip()!='N/A':
            return [Finding('DINGO-music-forbidden','ERROR','The source forbids background music; preserve non_diegetic_music: N/A. Ambient sound is separate.')]
    return []
