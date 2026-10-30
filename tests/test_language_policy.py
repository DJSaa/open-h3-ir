"""Regressions from Chinese FL2VA speech requests, no model/GPU required."""
import pytest
from types import SimpleNamespace
from h3ir.language import prepare, description_language, language_findings
from h3ir.models import Brief, DialogueLine, Mode, AssetRef, AssetKind, Role, AssetCard
from h3ir.compile import compile_brief

@pytest.mark.parametrize('intent,description,speech_language,text',[
 ('她看着镜头用普通话说：“准备好了，现在开始。”','Chinese','Chinese','准备好了，现在开始。'),
 ('She says in English: "Ready, let us begin."','English','English','Ready, let us begin.'),
 ('她用英语说：“Ready, let us begin.”','Chinese','English','Ready, let us begin.'),
 ('She says in Mandarin: “准备好了，现在开始。”','English','Chinese','准备好了，现在开始。'),
])
def test_independent_description_and_spoken_languages(intent,description,speech_language,text):
 b=prepare(Brief(intent=intent));assert b.description_language==description
 assert [(d.text,d.language) for d in b.dialogue]==[(text,speech_language)]

def test_subtitle_is_not_speech_and_speech_not_subtitle():
 b=prepare(Brief(intent='画面字幕显示“开场”。她用普通话说“准备好了，现在开始。”'))
 assert [d.text for d in b.dialogue]==['准备好了，现在开始。']
 assert not prepare(Brief(intent='不要说“你好”。')).dialogue

@pytest.mark.parametrize('mode',list(Mode))
def test_chinese_draft_and_curly_quotes_are_valid(monkeypatch,mode):
 import h3ir.compile as module
 assets=[];cards={}
 if mode in (Mode.I2VA,Mode.FL2VA,Mode.L2VA,Mode.REF2VA):
  count=2 if mode==Mode.FL2VA else 1
  for i in range(count):
   role=(Role.FRAME_ANCHOR_FIRST if i==0 else Role.FRAME_ANCHOR_LAST) if mode==Mode.FL2VA else (Role.FRAME_ANCHOR_LAST if mode==Mode.L2VA else Role.SUBJECT)
   key=str(i);assets.append(AssetRef(kind=AssetKind.IMAGE,role=role,sha256=key,px=(1344,768)))
   cards[key]=AssetCard(sha256=key,kind=AssetKind.IMAGE,style='Live-action',subjects=[{'kind':'person','descriptor':'the woman','attributes':['black top']}])
 monkeypatch.setattr(module,'analyse_all',lambda *a,**k:cards)
 monkeypatch.setattr(module,'measure_assets',lambda *a:None)
 backend=SimpleNamespace(require_available=lambda:None,server_version=lambda:'test',cfg=SimpleNamespace(model='test'))
 doc=compile_brief(Brief(intent='固定镜头，她用普通话说：“准备好了，现在开始。”',mode=mode,assets=assets),backend=backend,llm=False)
 assert doc.ok,doc.errors
 assert '<d>[Chinese] 准备好了，现在开始。</d>' in doc.prompt
 assert doc.provenance['description_language']=='Chinese'
 assert not language_findings(doc.prompt,'Chinese',(('准备好了，现在开始。','Chinese'),))


def test_wrong_spoken_language_is_error():
 f=language_findings('integrated_multimodal_description: [Shot 1] She says <d>[English] 你好。</d>\noverall_soundscape: A quiet room.\nnon_diegetic_music: N/A','English',(('你好。','Chinese'),))
 assert any(x.rule=='DINGO-dialogue-language' and x.severity=='ERROR' for x in f)

def test_wrong_description_language_is_error():
 f=language_findings('integrated_multimodal_description: [Shot 1] A woman turns around.\noverall_soundscape: A quiet room.\nnon_diegetic_music: N/A','Chinese')
 assert any(x.rule=='DINGO-description-language' for x in f)

@pytest.mark.parametrize('intent,language,line',[
 ('She says in English: “Ready, let us begin.”','English','Ready, let us begin.'),
 ('She says in Mandarin: “准备好了，现在开始。”','Chinese','准备好了，现在开始。'),
])
def test_english_draft_protects_inline_speech(monkeypatch,intent,language,line):
 import h3ir.compile as module
 monkeypatch.setattr(module,'analyse_all',lambda *a,**k:{})
 backend=SimpleNamespace(require_available=lambda:None,server_version=lambda:'test',cfg=SimpleNamespace(model='test'))
 doc=compile_brief(Brief(intent=intent,mode=Mode.T2VA),backend=backend,llm=False)
 assert doc.ok,doc.errors
 assert f'<d>[{language}] {line}</d>' in doc.prompt
 assert doc.prompt.count(line)==1
 assert not language_findings(doc.prompt,'English',((line,language),))


def test_speech_punctuation_is_byte_exact(monkeypatch):
 import h3ir.compile as module
 monkeypatch.setattr(module,'analyse_all',lambda *a,**k:{})
 backend=SimpleNamespace(require_available=lambda:None,server_version=lambda:'test',cfg=SimpleNamespace(model='test'))
 text='他说‘你好’，然后问：准备好了吗？'
 doc=compile_brief(Brief(intent='固定镜头。',mode=Mode.T2VA,dialogue=[DialogueLine(text)]),backend=backend,llm=False)
 assert doc.ok,doc.errors
 assert '<d>[Chinese] '+text+'</d>' in doc.prompt

@pytest.mark.parametrize('reply_language',['Chinese','English'])
def test_native_writer_and_wrong_language_fallback(monkeypatch,reply_language):
 import h3ir.compile as module
 from h3ir.grid import Target, instruction_line_for
 from h3ir.models import Role
 from h3ir.prose import compose_brief
 from h3ir.config import get_config
 class Writer:
  cfg=get_config().llm
  asks=[]
  def require_available(self):pass
  def server_version(self):return 'test'
  def chat(self,messages,**kw):
   self.asks.append(messages)
   sentence='镜头保持固定。她看向镜头，说道：{{D1}}' if reply_language=='Chinese' else 'The camera holds a static shot. She says {{D1}}'
   return SimpleNamespace(content='integrated_multimodal_description: [Shot 1] '+sentence+'\noverall_soundscape: '+('轻微环境声。' if reply_language=='Chinese' else 'A quiet room.')+'\nnon_diegetic_music: N/A')
 monkeypatch.setattr(module,'analyse_all',lambda *a,**k:{})
 writer=Writer();doc=compile_brief(Brief(intent='固定镜头，她用普通话说：“准备好了，现在开始。”',mode=Mode.T2VA),backend=writer)
 assert doc.ok,doc.errors
 assert '<d>[Chinese] 准备好了，现在开始。</d>' in doc.prompt
 assert not language_findings(doc.prompt,'Chinese',(('准备好了，现在开始。','Chinese'),))
 assert doc.source==('written' if reply_language=='Chinese' else 'draft')
 assert all('准备好了，现在开始。' not in str(messages) for messages in writer.asks)


def test_http_intent_only_chinese_speech_is_protected(monkeypatch):
 import h3ir.compile as module
 import h3ir.service as service
 from starlette.testclient import TestClient
 original=compile_brief
 backend=SimpleNamespace(require_available=lambda:None,server_version=lambda:'test',cfg=SimpleNamespace(model='test'))
 monkeypatch.setattr(module,'analyse_all',lambda *a,**k:{})
 monkeypatch.setattr(service,'compile_brief',lambda brief,**kw:original(brief,backend=backend,llm=False))
 response=TestClient(service.app).post('/v1/briefs',json={'intent':'固定镜头。她用普通话说：“准备好了，现在开始。”'})
 assert response.status_code==201,response.text
 body=response.json();assert '<d>[Chinese] 准备好了，现在开始。</d>' in body['ir']['prompt']
 assert body['ir']['provenance']['description_language']=='Chinese'


def test_native_chinese_reference_writer(monkeypatch):
 import h3ir.compile as module
 from h3ir.config import get_config
 reply='''subject_definitions:
<Subject 1> 是<Picture 1>中的人物，保留黑色上衣。

summary:
[reference generation] 参考人物在原场景中说话。

retention_analysis:
<Subject 1> (appears in [Shot 1]): fully_preserved - 外貌和黑色上衣保持一致。

detailed_description:
写实摄影风格，光线柔和。
[Shot 1] <Subject 1>看向镜头，黑色上衣保持一致。镜头保持固定。人物说道：{{D1}}

overall_soundscape:
轻微室内环境声。

non_diegetic_music:
N/A
'''
 class Writer:
  cfg=get_config().llm
  def require_available(self):pass
  def server_version(self):return 'test'
  def chat(self,*a,**kw):return SimpleNamespace(content=reply)
 asset=AssetRef(kind=AssetKind.IMAGE,role=Role.SUBJECT,sha256='a',px=(512,288))
 card=AssetCard(sha256='a',kind=AssetKind.IMAGE,subjects=[{'kind':'person','descriptor':'the woman','attributes':['black top']}])
 monkeypatch.setattr(module,'analyse_all',lambda *a,**k:{'a':card});monkeypatch.setattr(module,'measure_assets',lambda *a:None)
 doc=compile_brief(Brief(intent='人物用普通话说：“准备好了，现在开始。”',assets=[asset],mode=Mode.REF2VA),backend=Writer())
 assert doc.ok,doc.errors
 assert doc.source=='written',doc.fallback_reason
 assert '<d>[Chinese] 准备好了，现在开始。</d>' in doc.prompt


def test_one_english_section_cannot_hide_in_chinese_prompt():
 text='''subject_definitions:
<Subject 1> is a woman in a black shirt from <Picture 1>.
summary:
[reference generation] 人物在办公室中说话。
retention_analysis:
<Subject 1>: fully_preserved - 外貌保持一致。
detailed_description:
写实摄影。[Shot 1] 镜头保持固定，人物看向前方。
overall_soundscape:
轻微环境声。
non_diegetic_music:
N/A'''
 assert any(x.rule=='DINGO-description-language' and 'subject_definitions' in x.msg for x in language_findings(text,'Chinese'))


def test_two_speakers_keep_distinct_ids_and_exact_lines():
 from h3ir.language import restore_dialogue
 b=prepare(Brief(intent='甲说“你好。”乙说“开始。”'))
 text=restore_dialogue('{{D1}} {{D2}}',b)
 assert '(S1)用普通话说：<d>[Chinese] 你好。</d>' in text
 assert '(S2)用普通话说：<d>[Chinese] 开始。</d>' in text
 assert '{{D' not in text


def test_missing_inline_speech_cannot_ship_as_written(monkeypatch):
 import h3ir.compile as module
 from h3ir.config import get_config
 class Writer:
  cfg=get_config().llm
  def require_available(self):pass
  def server_version(self):return 'test'
  def chat(self,*a,**kw):
   return SimpleNamespace(content='integrated_multimodal_description: [Shot 1] 镜头保持固定。人物看向镜头。\noverall_soundscape: 轻微室内环境声。\nnon_diegetic_music: N/A')
 monkeypatch.setattr(module,'analyse_all',lambda *a,**k:{})
 doc=compile_brief(Brief(intent='固定镜头，她说：“准备好了，现在开始。”',mode=Mode.T2VA),backend=Writer())
 assert doc.ok,doc.errors
 assert doc.source=='draft'
 assert '<d>[Chinese] 准备好了，现在开始。</d>' in doc.prompt
 assert doc.fallback_reason
