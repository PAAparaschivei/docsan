"""Synthetic regressions from the local review workflow, never corporate fixtures."""
import hashlib
from pathlib import Path

import imagekit
import leakmatrix as lm
import pytest
from docsan import steps, verify
from docsan.docx.read import read_docx
from docsan.image.helper import call
from docsan.image.regions import field_masks, visual_masks
from docsan.image.text import ImageText, Line, Mask, Reading, _overlap
from docsan.match import Labeler, Matcher
from docsan.pdf import plan as planner
from docsan.pdf.read import Page
from docsan.pipeline import run_batch
from docsan.profile import Profile, Term
from docsan.review import Lexicon


def line(value, x, y, w=200, h=20):
    box=(x,y,w,h)
    chars=[(x+i*w/len(value),y,w/len(value),h) for i in range(len(value))]
    return Line(box,[Reading(value,1,chars)],0,True)


def test_masked_person_does_not_survive_in_hidden_pdf_text(monkeypatch,tmp_path):
    profile=Profile('Example','v1',[Term('Elena Marchetti','PERSON')],person_style='first_name_initial')
    ocr=ImageText(800,600,1,1,False,[line('Owner Elena Marchetti signed',20,100,500)])
    monkeypatch.setattr(planner,'read_text',lambda *a,**k:ocr)
    monkeypatch.setattr(planner,'fit_to_ink',lambda path,masks,*args,**kwargs:masks)
    monkeypatch.setattr(planner,'second_look',lambda *args:[])
    monkeypatch.setattr(planner,'visual_masks',lambda *args,**kwargs:[])
    result=planner.plan_page(Page(1,tmp_path/'page.png',800,600,(800,600),[]),'sha',Matcher(profile,set()),Labeler(),{})
    assert 'Elena' not in ' '.join(result.info['visible'])
    assert 'Marchetti' not in ' '.join(result.texts)
    assert 'Owner' in ' '.join(result.texts) and 'signed' in ' '.join(result.texts)


def test_field_masks_preserve_labels_and_financial_context():
    source=ImageText(1000,700,1,1,False,[line('Customer',20,40,90),line('Seabright Partners',160,40),
        line('23 RUE DES PINS',20,100),line('Seabright Holdings Ltd',20,160),
        line('Amount 450 EUR',20,220),line('Odoo catalog',20,280)])
    masks=field_masks(source)
    assert len(masks)==3
    for item in source.lines[1:4]:
        assert any(_overlap(item.box,m.box)>.95 for m in masks)
    for item in [source.lines[0],*source.lines[4:]]:
        assert not any(_overlap(item.box,m.box)>.1 for m in masks)


def test_numeric_ids_and_chart_ticks_are_distinct():
    matcher=Matcher(Profile('Example','v1',[Term('Example','ORG')]),set())
    assert [h.label for h in matcher.find('Account 9876543210 and SUB1234567').hits]==['ID','ID']
    for kept in ('Ticks 21 22 23 24', 'Amount 12345678 EUR', 'Azure 365 and S3 v2', '2021 2022 2023 2024'):
        assert not matcher.find(kept).hits, kept
        assert not verify._pattern_findings(kept.lower(),set(),True), kept
    assert any(h.label=='PHONE' for h in matcher.find('Call 26 12 34 56').hits)
    assert verify._pattern_findings('account 9876543210',set(),True)
    assert matcher.find('Annex 2024/05-4321').hits[0].label == 'ID'
    assert verify._pattern_findings('annex 2024/05-4321',set(),True)
    assert not matcher.find('Date 2024/05-12').hits
    with steps.disabled('detect-identifiers'):
        assert not matcher.find('Account 9876543210').hits
    with verify.step_disabled('identifiers'):
        assert not verify._pattern_findings('account 9876543210',set(),True)


def test_classified_compound_system_is_not_hidden_pending(tmp_path):
    from docsan.docx.model import Document, Paragraph, Run
    from docsan.docx.write import write_docx
    src=tmp_path/'system.docx';write_docx(Document([Paragraph([Run('Run setup.py first.')])]),src)
    profile=lm.write_profile(tmp_path/'profile.json',{'terms':lm.base_terms()+[{'text':'setup.py','label':'SYSTEM'}]})
    result=run_batch([src],profile,None,tmp_path/'private',lexicon=Lexicon.load(lm.LEXICON),release=False).files[0]
    assert result.review==[], result.review
    assert result.status=='ready',result.reasons


@pytest.mark.parametrize('referenced',[False,True])
def test_only_unreferenced_narrow_trash_parts_are_dropped(tmp_path,referenced):
    from docsan.docx.model import Document, Paragraph, Run
    from docsan.docx.write import package_bytes
    parts=package_bytes(Document([Paragraph([Run('Integration overview')])]))
    parts['[trash]/0000.dat']=b'fictional discarded bytes'
    if referenced:
        parts['_rels/.rels']=parts['_rels/.rels'].replace(b'</Relationships>',b'<Relationship Id="rTrash" Type="urn:unknown" Target="%5Btrash%5D/0000.dat"/></Relationships>')
    src=tmp_path/'trash.docx';src.write_bytes(lm.zip_bytes(parts));before=hashlib.sha256(src.read_bytes()).hexdigest()
    result=read_docx(src)
    assert (result.document is None)==referenced
    if not referenced:
        assert result.dropped['unreferenced package trash']==1
    assert hashlib.sha256(src.read_bytes()).hexdigest()==before


def test_brand_component_is_covered_without_adjacent_heading(tmp_path):
    imagekit.helper()
    path=tmp_path/'brand.png'
    imagekit.render({'output':str(path),'width':1000,'height':700,'items':[
        {'shape':'ellipse','box':[30,30,100,100],'color':[.1,.6,.9],'stroke':12},
        {'text':'Aurelan','x':70,'y':65,'size':28},
        {'text':'Architecture overview','x':260,'y':65,'size':28}]})
    text=ImageText(1000,700,1,1,False,[line('Aurelan',70,65,100,28),line('Architecture overview',260,65,290,28)])
    masks=visual_masks(path,text,[Mask((70,65,100,28),'CLIENT','text')])
    logo=[m for m in masks if m.label=='LOGO']
    assert logo and _overlap((30,30,100,100),logo[0].box)>.9
    assert _overlap(text.lines[1].box,logo[0].box)==0
    assert 'payload' not in str(call({'command':'regions','input':str(path)})).lower()


def test_detected_barcode_is_covered_without_exposing_payload(tmp_path):
    import json
    import subprocess

    from docsan.pdf.write import png_rows
    maker=imagekit._build(Path(__file__).parent/'tools'/'make-code.swift','make-code')
    path=tmp_path/'code.png'
    subprocess.run([str(maker),str(path)],check=True)
    observed=call({'command':'regions','input':str(path)})
    assert len(observed['barcodes'])==1 and 'fictional' not in json.dumps(observed)
    width,height,_colors,_rows=png_rows(path.read_bytes())
    masks=visual_masks(path,ImageText(width,height,1,1,False,[]),[])
    assert len(masks)==1 and masks[0].label=='CODE'
    target=tmp_path/'masked.png'
    call({'command':'mask','input':str(path),'output':str(target),'format':'png','boxes':[list(m.box) for m in masks]})
    assert call({'command':'regions','input':str(target)})['barcodes']==[]


def test_dense_ocr_selection_compares_local_candidates(monkeypatch):
    from docsan.image import text as image_text
    count=0
    overlap=image_text._overlap
    def measured(a,b):
        nonlocal count
        count+=1
        return overlap(a,b)
    monkeypatch.setattr(image_text,'_overlap',measured)
    lines=[]
    for y in range(60):
        for x in range(30):
            item=line('Repeated value',x*150,y*50,110,20)
            item.tile=True
            lines.append(item)
    source=ImageText(4600,3100,1,1,False,lines)
    assert source.best()==lines
    assert count < len(lines)*100, count


def test_amount_decimal_survives_a_weaker_ocr_reading():
    from docsan.image.text import plan_masks
    amount=line('4767,4500',700,120,100,20)
    amount.readings.append(Reading('47674500',.7,amount.readings[0].chars[:8]))
    text=ImageText(1000,700,1,1,False,[line('EUR',750,50,50,20),amount,line('9876543210',20,120,110,20)])
    matcher=Matcher(Profile('Example','v1',[Term('Example','ORG')]),set())
    masks,_=plan_masks(text,matcher)
    assert any(_overlap(m.box,text.lines[2].box)>.9 for m in masks)
    assert not any(_overlap(m.box,amount.box)>.1 for m in masks)


def test_short_form_record_title_is_masked_but_chart_ticks_stay():
    record=line('07071',400,40,100,32)
    text=ImageText(1000,700,1,1,False,[record,line('Order Date',400,110,100,16),line('Product',400,160,100,16)])
    assert any(m.label=='ID' and _overlap(m.box,record.box)>.9 for m in field_masks(text))
    assert field_masks(ImageText(1000,700,1,1,False,[record,line('Amount',400,110),line('Revenue',400,160)]))==[]


def test_scaled_regions_do_not_change_cached_helper_answers(monkeypatch):
    import copy

    from docsan.image import regions
    raw={'barcodes':[[200,200,40,40]],'code_candidates':[],'colour_components':[[60,60,200,200]]}
    before=copy.deepcopy(raw)
    monkeypatch.setattr(regions,'call',lambda request:raw)
    text=ImageText(1000,700,1,1,False,[line('Aurelan',70,65,100,28)])
    seed=[Mask((70,65,100,28),'CLIENT','text')]
    first=visual_masks('unused',text,seed,scale=.5)
    assert visual_masks('unused',text,seed,scale=.5)==first
    assert raw==before


def test_unpositioned_customer_text_does_not_survive_field_mask(monkeypatch,tmp_path):
    profile=Profile('Example','v1',[Term('Example','ORG')])
    value='Seabright Holdings Ltd'
    ocr=ImageText(800,600,1,1,False,[Line((20,100,400,30),[Reading(value,1,[None]*len(value))],0,True)])
    monkeypatch.setattr(planner,'read_text',lambda *a,**k:ocr)
    monkeypatch.setattr(planner,'fit_to_ink',lambda path,masks,*args,**kwargs:masks)
    monkeypatch.setattr(planner,'second_look',lambda *args:[])
    monkeypatch.setattr(planner,'visual_masks',lambda *args,**kwargs:[])
    result=planner.plan_page(Page(1,tmp_path/'page.png',800,600,(800,600),[]),'sha',Matcher(profile,set()),Labeler(),{})
    assert result.masks and 'Seabright' not in ' '.join(result.texts)
    assert verify._pdf_field_findings('Seabright Holdings Ltd')
    assert verify._pdf_field_findings('23 RUE DES PINS')
    with verify.step_disabled('pdf-field-values'):
        assert not verify._pdf_field_findings('Seabright Holdings Ltd')


@pytest.mark.parametrize('value',['C2 - Internal','C3 — Confidentiel','C2 Internal','C3: Confidential','C4 Restreinte'])
def test_coded_classification_is_masked_and_verified(value):
    matcher=Matcher(Profile('Example','v1',[Term('Example','ORG')]),set())
    hits=matcher.find(value).hits
    assert len(hits)==1 and hits[0].label=='LABEL'
    assert hits[0].start==0 and hits[0].end==len(value)
    view=verify._vnorm(value)
    assert verify._pattern_findings(view,set(),True)
    with steps.disabled('detect-classification'):
        assert not matcher.find(value).hits
    with verify.step_disabled('classification-labels'):
        assert not verify._pattern_findings(view,set(),True)
    for kept in ('C2 server supports internal routing','Internal service','C3 API version'):
        assert not matcher.find(kept).hits
        assert not verify._pattern_findings(kept.lower(),set(),True)


def test_date_icon_and_price_column_are_not_phones():
    matcher=Matcher(Profile('Example','v1',[Term('Example','ORG')]),set())
    for kept in ('Invoice + 31/10/2021','Unit Price 59.16 59.16 21.42'):
        assert not matcher.find(kept).hits
        assert not verify._pattern_findings(kept.lower(),set(),True)
    for phone in ('Call +31 20 123 4567','Call +352 26.12.34.56','Call 26 12 34 56'):
        assert any(h.label=='PHONE' for h in matcher.find(phone).hits)
        assert verify._pattern_findings(phone.lower(),set(),True)


def test_leading_dot_domain_suffix_is_redacted_and_independently_found():
    matcher=Matcher(Profile('Example','v1',[]),set())
    value='The certificate covers .internal.aurelan.lu for this environment.'
    hits=matcher.find(value).hits
    assert len(hits)==1 and hits[0].label=='DOMAIN'
    assert value[hits[0].start:hits[0].end]=='.internal.aurelan.lu'
    assert verify._pattern_findings(value.lower(),set(),True)


def test_leading_dot_kept_system_filename_stays_kept():
    matcher=Matcher(Profile('Example','v1',[Term('setup.py','SYSTEM')]),set())
    assert not matcher.find('.setup.py').hits
    assert not verify._pattern_findings('.setup.py',{'setup.py'},True)
