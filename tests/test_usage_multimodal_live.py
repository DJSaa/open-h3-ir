"""Opt-in live Qwen evidence: actual images/video frames contribute to input usage."""
import json
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace
import httpx
import pytest
from h3ir.backend import Backend, user_message
from h3ir.config import Config, LLMConfig
from h3ir.usage import track_compilation

@pytest.mark.skipif(os.getenv('H3IR_LIVE_USAGE_TEST')!='1',reason='requires an explicitly enabled live vision endpoint and source video')
def test_live_multimodal_usage_matches_real_responses(tmp_path):
    video=Path(os.environ['H3IR_USAGE_VIDEO'])
    output=Path(os.getenv('H3IR_USAGE_EVIDENCE_DIR',str(tmp_path)));output.mkdir(parents=True,exist_ok=True)
    ffmpeg=os.getenv('H3IR_USAGE_FFMPEG','ffmpeg')
    frames=[]
    for i,t in enumerate((0.8,4.0,7.2)):
        path=output/f'frame-{i}.png'
        subprocess.run([ffmpeg,'-y','-v','error','-ss',str(t),'-i',str(video),'-frames:v','1',str(path)],check=True,capture_output=True)
        frames.append(path)
    receipts=[]
    def capture(response):
        if response.request.url.path.endswith('/chat/completions'):
            response.read();receipts.append(response.json())
    cfg=Config(llm=LLMConfig(base_url=os.environ['H3IR_LLM_URL'],model=os.environ['H3IR_LLM_MODEL'],max_tokens=64,timeout_s=180))
    cases=[('text_only',[]),('one_image',frames[:1]),('video_three_frames',frames)]
    with httpx.Client(trust_env=False,timeout=180,event_hooks={'response':[capture]}) as client:
        backend=Backend(cfg,client=client)
        @track_compilation
        def run():
            for name,images in cases:
                backend.chat([{'role':'system','content':'You are a visual assistant.'},user_message('Describe the visual content in one short sentence. If there is no image, say no image.',images)],max_tokens=64,thinking=False,seed=7,retries=0)
            return SimpleNamespace(provenance={})
        usage=run().provenance['usage']
    evidence={'cases':[{'name':name,'image_count':len(images),'response':response} for (name,images),response in zip(cases,receipts)],'aggregate':usage,
              'scope':'Counts include visual tokens plus image markers; this does not isolate vision-only token IDs.'}
    (output/'evidence.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2))
    assert len(receipts)==3
    values=[r['usage'] for r in receipts]
    # These margins exclude a mere few textual image labels being mistaken for vision accounting.
    assert values[1]['prompt_tokens']>values[0]['prompt_tokens']+32
    assert values[2]['prompt_tokens']>values[1]['prompt_tokens']+64
    assert usage['prompt_tokens']==sum(x['prompt_tokens'] for x in values)
    assert usage['completion_tokens']==sum(x['completion_tokens'] for x in values)
    assert usage['total_tokens']==usage['prompt_tokens']+usage['completion_tokens']
    assert usage['llm_requests']==3 and usage['complete']
