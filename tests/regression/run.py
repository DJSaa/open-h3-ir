# SPDX-FileCopyrightText: Copyright (c) 2026 Dingo contributors
# SPDX-License-Identifier: Apache-2.0
"""Explicit opt-in, serial HTTP compilation regression; never generates video."""
import argparse
import copy
import hashlib
import json
import mimetypes
import os
from pathlib import Path
import time
import uuid
from urllib.parse import urlsplit
import httpx
from common import ROOT, asset_path, classify, load, owned, save, select_cases
from report import report


def execute(client, case, request, files, transport, folder, state, timeout, poll_s):
    if state.get('phase') == 'submitting':
        # A direct synchronous compile has no idempotency/query contract. Never
        # silently re-submit a request whose response may have been lost.
        if transport == 'direct':
            return 0, {}, 'EXECUTION_UNKNOWN'
    if transport == 'gateway' and state.get('task_id'):
        code, body = 200, {'id':state['task_id'], 'status':'in_progress'}
    else:
        state['phase'] = 'preparing'
        save(folder/'state.json', state)
        if transport == 'direct':
            for path, metadata in files:
                response = client.put('/v1/assets/'+metadata['asset_id'], content=path.read_bytes(), headers={'Content-Type':'application/octet-stream'})
                if response.status_code not in (200, 201):
                    return response.status_code, {}, 'FAIL_ASSET_UPLOAD'
        state['phase'] = 'submitting'
        save(folder/'state.json', state)
        if transport == 'direct':
            response = client.post('/v1/briefs', json=request['direct'])
        else:
            uploads = [('input_references',(metadata['filename'],path.read_bytes(),mimetypes.guess_type(metadata['filename'])[0] or 'application/octet-stream')) for path,metadata in files]
            fields = [(name,(None,value)) for name,value in request['gateway_fields'].items()]
            response = client.post('/v1/context-ir', files=fields+uploads, headers={'Idempotency-Key':state['idempotency_key']})
        code = response.status_code
        try:
            body = response.json()
        except ValueError:
            return code, {}, 'FAIL_PROTOCOL'
        if transport == 'gateway' and code in (200, 202) and isinstance(body,dict) and body.get('id'):
            state['task_id'] = body['id']
            state['phase'] = 'polling'
            save(folder/'state.json', state)
    if transport == 'gateway' and code in (200, 202) and isinstance(body,dict) and body.get('id'):
        deadline = time.monotonic()+timeout
        while body.get('status') not in ('completed','failed','cancelled','expired'):
            if time.monotonic() >= deadline:
                return code, body, 'POLL_TIMEOUT'
            response = client.get('/v1/context-ir/'+state['task_id'])
            code = response.status_code
            try: body = response.json()
            except ValueError: return code, {}, 'FAIL_PROTOCOL'
            if code != 200: break
            if body.get('status') not in ('completed','failed','cancelled','expired'): time.sleep(poll_s)
    return code, body, classify(case, code, body, transport)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--transport',choices=('direct','gateway'),default='direct')
    parser.add_argument('--base-url',required=True,help='Service origin only, e.g. https://compiler.example.com')
    parser.add_argument('--token-env',default='REGRESSION_TOKEN',help='Environment variable holding an optional bearer token')
    parser.add_argument('--profile')
    parser.add_argument('--cases',nargs='+')
    parser.add_argument('--suite',choices=('comprehensive','prompt_quality'))
    parser.add_argument('--seed',type=int,help='Override compilation seed, not video seed')
    parser.add_argument('--model',help='Gateway non-Ref2VA model binding')
    parser.add_argument('--ref-model',help='Gateway Ref2VA model binding')
    parser.add_argument('--output',type=Path)
    parser.add_argument('--resume',action='store_true')
    parser.add_argument('--timeout',type=float,default=900)
    parser.add_argument('--poll-s',type=float,default=2)
    args = parser.parse_args(argv)
    url = urlsplit(args.base_url)
    if url.scheme not in ('http','https') or not url.hostname or url.username or url.password or url.path not in ('','/') or url.query or url.fragment:
        parser.error('--base-url must be an HTTP(S) origin without credentials, path or query')
    if args.timeout <= 0 or args.poll_s <= 0: parser.error('timeouts must be positive')
    cases = select_cases(args.profile,args.cases,args.suite)
    output = args.output or ROOT/'outputs'/(time.strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:8])
    if output.exists() and not args.resume: parser.error('output already exists; choose a new folder or --resume')
    manifest = load(ROOT/'assets/manifest.json')
    identity = {'transport':args.transport,'endpoint_sha256':hashlib.sha256(args.base_url.rstrip('/').encode()).hexdigest(),
                'cases':[c['id'] for c in cases],'seed':args.seed,'model':args.model,'ref_model':args.ref_model,
                'inputs_sha256':hashlib.sha256(json.dumps([load(owned(ROOT,c['request_file'])) for c in cases],sort_keys=True).encode()).hexdigest()}
    if args.resume:
        prior=load(output/'run.json')
        if prior['identity']!=identity: parser.error('resume configuration or frozen inputs changed')
        run_id=prior['run_id']
    else:
        run_id=uuid.uuid4().hex
        save(output/'run.json',{'run_id':run_id,'identity':identity,'concurrency':1,'video_generation':False})
    token=os.environ.get(args.token_env)
    headers={'Authorization':'Bearer '+token} if token else {}
    with httpx.Client(base_url=args.base_url.rstrip('/'),headers=headers,timeout=args.timeout,trust_env=False) as client:
        for case in cases:
            folder=output/'cases'/case['id']
            if args.resume and (folder/'result.json').exists():
                previous=load(folder/'result.json')
                retryable = previous['outcome'] in ('POLL_TIMEOUT','BLOCKED_ASSET','FAIL_TRANSPORT')
                retryable |= args.transport == 'gateway' and previous['outcome'] == 'EXECUTION_UNKNOWN'
                if not retryable: continue
            request=copy.deepcopy(load(owned(ROOT,case['request_file'])))
            if args.seed is not None:
                request['direct']['seed']=args.seed
                request['gateway_fields']['context_ir_seed']=str(args.seed)
            model=args.ref_model if case['mode']=='ref2va' else args.model
            if model:request['gateway_fields']['model']=model
            save(folder/'request.json',request)
            save(folder/'expectations.json',load(owned(ROOT,case['expectations_file'])))
            state=load(folder/'state.json') if (folder/'state.json').exists() else {'idempotency_key':'ir-regression-'+run_id+'-'+case['id']}
            start=time.monotonic()
            try:
                files=[(asset_path(manifest[f['asset_id']]),f) for f in request['files']]
                code,body,outcome=execute(client,case,request,files,args.transport,folder,state,args.timeout,args.poll_s)
            except FileNotFoundError:
                code,body,outcome=0,{},'BLOCKED_ASSET'
            except (httpx.HTTPError,ValueError,TypeError,KeyError) as exc:
                # Exception strings may contain origin addresses or credentials.
                code,body,outcome=0,{'error_type':type(exc).__name__},'EXECUTION_UNKNOWN' if state.get('phase')=='submitting' else 'FAIL_TRANSPORT'
            save(folder/'body.json',body)
            result={'id':case['id'],'transport':args.transport,'http':code,'outcome':outcome,
                    'elapsed_s':round(time.monotonic()-start,3),'semantic_quality':'NOT_REVIEWED',
                    'usage':body.get('usage') if isinstance(body,dict) else None}
            save(folder/'result.json',result)
            report(output)
            print(case['id'],outcome,result['elapsed_s'],flush=True)
    summary=report(output)
    print(json.dumps(summary,ensure_ascii=False,indent=2))
    return int(any(not key.startswith('PASS_') for key in summary['outcomes']))


if __name__=='__main__':
    raise SystemExit(main())
