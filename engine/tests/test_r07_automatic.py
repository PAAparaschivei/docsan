"""No per-document overrides: source facts, cropped fields and public syntax."""
from __future__ import annotations

import leakmatrix as lm
from docsan import verify
from docsan.docx.model import Document, Paragraph, Run
from docsan.docx.write import write_docx
from docsan.image.regions import continuation_masks, footer_masks
from docsan.image.text import ImageText, _overlap, plan_masks, visible_crop_lines
from docsan.match import Matcher
from docsan.profile import Profile, Term
from test_r05_regions import coverage, line


def matcher():
    return Matcher(Profile("Example", "v1", []), set())


def test_host_facts_are_document_local_and_cover_bare_repetitions():
    original = matcher()
    source = ["VM CLOUDNODEA02 in UAT", "Serveur INFRA-NODE-01", "root@CLOUDDBP01 scripts]$"]
    local = original.with_context(source)
    for text in ("CLOUDNODEA02-ETL", "infra-node-01", "cloudDBP01"):
        assert any(h.label == "DOMAIN" for h in local.find(text).hits)
        assert not original.find(text).hits
    assert local.profile.terms == [] and local.profile.safe_words == []
    assert not original.with_context(["Server PostgreSQL13; Odoo17; Python3"]).find("PostgreSQL13 Odoo17 Python3").hits


def test_host_cues_support_hyphens_and_prompts_without_product_false_positives():
    for text in ("Machine: CLOUDNODE01", "server INFRA-NODE-01", "root@CLOUDDBP01 scripts]$"):
        assert any(h.label == "DOMAIN" for h in matcher().find(text).hits)
        assert verify._source_host_names([text], set())
    for text in ("Server PostgreSQL13", "Odoo v17", "Public ProductP01", "Version 7.7.0"):
        assert not matcher().find(text).hits
        assert not verify._source_host_names([text], set())


def test_sql_and_public_log_names_need_no_exact_keep_decision():
    for text in ("select ir_cron.id from cron_log join ir_cron on cron_log.id = ir_cron.id",
                 "INFO odoo.addons.base.models.ir_cron: Starting job",
                 "INFO addons.base.models.ir_cron: Starting job",
                 "INFO base.models.ir: Job completed"):
        assert not matcher().find(text).hits
        assert not verify._pattern_findings(text.lower(), set(), True)
    for text in ("system.ca", "base.models.ir", "select 'jobs.ca' from jobs", "INFO see customer.example"):
        assert verify._pattern_findings(text.lower(), set(), True)
    assert matcher().find("select 'jobs.ca' from jobs").hits


def test_system_variables_are_exempt_only_inside_their_column():
    rows = [line("System Variables", (850, 100, 130, 14)),
            line("system.ca", (850, 140, 110, 14)),
            line("system.ca", (100, 140, 110, 14)),
            line("https://system.ca", (850, 200, 130, 14))]
    masks, _ = plan_masks(ImageText(1100, 500, 1, 1, False, rows), matcher())
    assert all(_overlap(m.box, rows[1].box) == 0 for m in masks)
    assert any(_overlap(m.box, rows[2].box) > .9 for m in masks)
    assert any(_overlap(m.box, rows[3].box) > .9 for m in masks)
    ocr = [verify.OcrLine(l.readings[0].text, list(l.box)) for l in rows]
    found, boxes = verify._scan_lines(ocr, [], set(), "fixture")
    assert found and rows[1].box not in [tuple(b) for b in boxes]
    assert rows[2].box in [tuple(b) for b in boxes] and rows[3].box in [tuple(b) for b in boxes]


def test_explicit_company_rule_still_applies_inside_public_code():
    m = Matcher(Profile("Example", "v1", [Term("system", "CLIENT")]), set())
    assert any(h.label == "CLIENT" for h in m.find("system.ca", system_variables=True).hits)
    rules = [verify._build_rule("system", False, True, [])]
    assert verify._scan_lines([verify.OcrLine("system.ca", [850, 140, 100, 14])], rules, set(), "fixture")[0]


def test_cropped_transport_row_inherits_aligned_field_and_keeps_neighbors():
    rows = [line("SFTP CLOUDNODE01", (440, 600, 180, 12)),
            line("SFTP CLOUDNODE02", (440, 680, 180, 12)),
            line("QFTP CLOUDTEMP01", (440, 760, 180, 8)),
            line("RES=0", (440, 740, 60, 12)), line("Amount 450 EUR", (780, 760, 150, 16))]
    text = ImageText(1100, 800, 1, 1, False, rows)
    masks = continuation_masks(text)
    target = (500, 760, 120, 8)
    assert masks and any(coverage(m.box, target) > .9 for m in masks)
    assert all(_overlap(m.box, l.box) == 0 for m in masks for l in rows[-2:])
    assert continuation_masks(ImageText(1100, 800, 1, 1, False, rows[1:])) == []
    distant = [line("SFTP CLOUDNODE01",(440,300,180,12)), line("SFTP CLOUDNODE02",(440,400,180,12)), rows[2]]
    assert continuation_masks(ImageText(1100,800,1,1,False,distant)) == []
    # Same opaque token elsewhere in the screenshot has no inferred field type.
    assert continuation_masks(ImageText(1100, 800, 1, 1, False, [*rows[:2], line("QFTP CLOUDTEMP01", (100,760,180,8))])) == []


def test_independent_verifier_blocks_unmasked_transport_continuation():
    rows = [verify.OcrLine("SFTP", [440,600,30,12]), verify.OcrLine("SFTP", [440,680,30,12]),
            verify.OcrLine("QFTP CLOUDTEMP01", [440,760,180,8])]
    found, boxes = verify._scan_lines(rows, [], set(), "fixture", verify._Picture(1100,800))
    assert any("transport-field" in f for f in found) and rows[-1].box in boxes
    assert not verify._scan_lines(rows[1:], [], set(), "fixture", verify._Picture(1100,800))[0]


def test_verifier_derives_source_host_rule_without_sanitizer_hits(tmp_path):
    profile = lm.write_profile(tmp_path / "profile.json")
    output = tmp_path / "bad.docx"
    write_docx(Document([Paragraph([Run("Reference: CLOUDNODEP01")])]), output)
    plain = verify.verify_docx(output, profile)
    assert plain.status == "pass", plain.findings
    check = verify.verify_docx(output, profile, source_texts=["Machine CLOUDNODEP01"])
    assert check.status == "findings" and any("term" in f for f in check.findings)
    assert not verify._source_host_names(["Public ProductP01; Server PostgreSQL13"], set())


def test_crop_context_excludes_hidden_content_and_maps_coordinates():
    source = ImageText(1000,800,1,1,False,[line("Hidden Company",(20,100,70,20)),
                     line("Visible field",(200,100,150,20))])
    mapped = visible_crop_lines(source,(.1,0,0,0),900,800)
    assert len(mapped) == 1 and mapped[0].readings[0].text == "Visible field"
    assert mapped[0].box == (100,100,150,20) and not mapped[0].corrected and mapped[0].context_only
    assert mapped[0].readings[0].chars[0][0] == 100
    import pytest
    with pytest.raises(ValueError,match="geometry"):
        visible_crop_lines(source,(.1,0,0,0),901,800)


def test_full_image_alternate_recovers_cropped_transport_host():
    rows=[line("SFTP CLOUDNODE01",(440,600,180,12)),line("SFTP CLOUDNODE02",(440,680,180,12))]
    cropped=ImageText(1100,800,1,1,False,rows)
    full=ImageText(1120,800,1,1,False,[line("QFTP CLOUDTEMP01",(460,760,180,8))])
    cropped.lines += visible_crop_lines(full,(20/1120,0,0,0),1100,800)
    assert len(cropped.best()) == 2
    assert continuation_masks(cropped)


def test_loose_alternate_word_box_cannot_swallow_neighboring_row():
    rows = [line("SFTP CLOUDNODE01",(440,600,180,12)), line("SFTP CLOUDNODE02",(440,680,180,12)),
            line("QFTP CLOUDTEMP01",(440,760,180,8))]
    reading = rows[-1].readings[0]
    reading.chars = [(420,740,220,60) for _ in reading.text]
    masks = continuation_masks(ImageText(1100,800,1,1,False,rows))
    assert masks and all(m.box[1] >= 757 and m.box[1]+m.box[3] <= 771 for m in masks)


def test_transport_continuation_does_not_expand_as_legal_footer():
    rows = [line("SFTP CLOUDNODE01",(440,600,180,12)), line("SFTP CLOUDNODE02",(440,680,180,12)),
            line("QFTP CLOUDTEMP01",(440,760,180,8)), line("RES=0",(440,740,60,12)),
            line("Copyright Example",(650,780,140,10))]
    text = ImageText(1100,800,1,1,False,rows)
    masks = continuation_masks(text)
    final = masks + footer_masks(text,masks)
    assert final and all(_overlap(m.box,l.box) == 0 for m in final for l in rows[-2:])


def test_pipeline_removes_repeated_hosts_without_profile_edits(tmp_path):
    from docsan.pipeline import run_batch
    from docsan.review import Lexicon
    src = tmp_path / "input.docx"
    src.write_bytes(lm.doc(lm.text_para("Machine CLOUDNODEP01") + lm.text_para("Reference CLOUDNODEP01-ETL")))
    profile = lm.write_profile(tmp_path / "profile.json")
    before = profile.read_bytes()
    result = run_batch([src],profile,tmp_path/"out",tmp_path/"private",
                       lexicon=Lexicon.load(lm.LEXICON, extra=("machine","reference","etl"))).files[0]
    assert result.status == "ready", result.reasons
    assert not result.review and profile.read_bytes() == before
    assert "CLOUDNODEP01" not in verify.extractor(tmp_path/"out"/result.output)


def test_crop_alternates_do_not_vote_for_preview_transcription():
    from dataclasses import replace
    best = replace(line("Integration",(100,100,100,20)),tile=True)
    weak = replace(line("integraton",(100,100,100,20)),tile=True)
    best.readings[0].confidence = .95
    weak.readings[0].confidence = .5
    t = ImageText(1000,800,1,1,False,[best,weak])
    full = ImageText(1000,800,1,1,False,[line("integraton",(100,100,100,20)) for _ in range(4)])
    t.lines += visible_crop_lines(full,(0,0,0,0),1000,800)
    assert t.best()[0].readings[0].text == "Integration"


def test_public_code_symbols_do_not_need_profile_corrections():
    from docsan.image.regions import reviewable_lines
    from docsan.review import Lexicon, unfamiliar_words
    m = matcher()
    text = "select ir_cron.id from ir_cron"
    assert "ir_cron.id" not in m.reviewable(text)
    assert m.find("system.ca").hits  # domain policy remains active
    rows = [line("System Variables",(850,100,130,14)),
            line("system.job.systemName",(850,140,180,14)),
            line("custom.PrivateName",(100,140,180,14))]
    values = reviewable_lines(ImageText(1100,500,1,1,False,rows),[],m)
    assert not values[1].strip()
    queued = unfamiliar_words(values,Lexicon({"system","variables","custom"}))
    assert any(q.word == "PrivateName" for q in queued)
    assert not any(q.word == "systemName" for q in queued)
    log = "INFO odoo.addons.base.models.ir: Starting job"
    assert not unfamiliar_words([m.reviewable(log)],Lexicon({"info","starting","job"}))


def test_remote_session_identity_bands_keep_menus_and_commands():
    from docsan.image.regions import screenshot_masks
    rows=[line("Sessions Tools Settings Macros",(80,20,240,10)),
          line("Detach tab",(40,60,60,10)),line("Disconnect",(550,60,60,10)),
          line("ConnectionAlias",(80,40,100,12)),line("Private Window",(20,2,120,10)),
          line("Business context",(100,100,200,16))]
    text=ImageText(1400,800,1,1,False,rows)
    masks=screenshot_masks(text)
    assert all(any(coverage(m.box,l.box)>.99 for m in masks) for l in rows[3:5])
    assert all(_overlap(m.box,l.box)==0 for m in masks for l in rows[:3]+rows[5:])
    assert not screenshot_masks(ImageText(1400,800,1,1,False,rows[1:]))
    ocr=[verify.OcrLine(l.readings[0].text,list(l.box)) for l in rows]
    found,boxes=verify._scan_lines(ocr,[],set(),"fixture",verify._Picture(1400,800))
    assert sum("remote-session identity" in f for f in found)==2
    assert all(list(l.box) not in boxes for l in rows[:3]+rows[5:])


def test_ocr_public_labels_are_scoped_to_known_ui_roles():
    from docsan.image.regions import reviewable_lines
    rows=[line("Sessions Tools Settings Macros",(80,20,240,10)),
          line("TerminaI",(5,20,60,10)),line("GoAnywhere",(100,140,90,14)),
          line("ENVIRONMEN",(210,140,80,10)),line("Component Library",(250,230,110,12)),
          line("Integrati0n",(270,350,80,10)),line("When File Exists",(700,400,110,12)),
          line("Overwrlte",(850,400,70,12)),line("CustomPrivateName",(270,380,120,10)),
          line("Integrati0n",(700,500,80,12)),line("Overwrlte",(850,460,70,12)),
          line("TerminaI",(600,600,70,12)),line("ENVIRONMEN",(600,650,80,12))]
    text=ImageText(1400,800,1,1,False,rows)
    values=reviewable_lines(text,[],matcher())
    for i in (1,3,5,7): assert not values[i].strip()
    for i in range(8,len(rows)): assert values[i] == rows[i].readings[0].text
    before=[l.readings[0].text for l in rows]
    reviewable_lines(text,[],matcher())
    assert [l.readings[0].text for l in rows] == before
    # Same unfamiliar words without the verified app frame still need decisions.
    assert reviewable_lines(ImageText(1400,800,1,1,False,rows[3:]),[],matcher())[2]
    explicit=Matcher(Profile("Example","v1",[Term("Integrati0n","CLIENT")]),set())
    assert plan_masks(text,explicit)[0]


def test_three_remote_commands_recover_a_partly_read_menu():
    from docsan.image.regions import screenshot_masks
    rows=[line("Sessions",(80,20,60,10)),line("Detach tab",(40,60,60,10)),
          line("Disconnect",(550,60,60,10)),line("Set connection password",(350,60,120,10)),
          line("ConnectionAlias",(80,40,100,12))]
    text=ImageText(1400,800,1,1,False,rows)
    assert any(coverage(m.box,rows[-1].box)>.99 for m in screenshot_masks(text))
    assert not screenshot_masks(ImageText(1400,800,1,1,False,rows[:3]+rows[-1:]))
    ocr=[verify.OcrLine(l.readings[0].text,list(l.box)) for l in rows]
    assert verify._scan_lines(ocr,[],set(),"fixture",verify._Picture(1400,800))[0]


def test_source_hosts_match_separator_variants_without_saved_aliases():
    m=matcher().with_context(["Server CLOUD-NODEP01", "VM CLOUDNODEP02"])
    for text in ["cloud_nodep01", "cloud.nodep01", "cloudnodep01", "CLOUDNODEP_02"]:
        assert m.find(text).hits
    assert not m.find("MyCLOUDNODEP01suffix").hits


def test_public_sql_symbols_survive_fragmented_queries_but_links_do_not():
    assert not verify._pattern_findings("ir_cron.id",set(),True)
    assert not matcher().find("ir_cron.id").hits
    assert verify._pattern_findings("https://ir_cron.id",set(),True)
    assert verify._pattern_findings("'ir_cron.id'",set(),True)
    assert verify._pattern_findings("private_table.id",set(),True)


def test_independent_ocr_restores_variable_rows_and_keeps_real_domains():
    rows=[verify.OcrLine("System",[850,100,55,14]),verify.OcrLine("Variables",[910,100,70,14]),
          verify.OcrLine("system.",[850,140,55,14]),verify.OcrLine("caller.id",[909,140,70,14]),
          verify.OcrLine("b.name",[850,180,60,14]),verify.OcrLine("customer.example",[850,220,130,14]),
          verify.OcrLine("b.name",[100,180,60,14])]
    found,boxes=verify._scan_lines(rows,[],set(),"fixture")
    assert len(found)==2
    assert rows[-1].box in boxes and rows[-2].box in boxes
    assert not verify._scan_lines(rows[:4],[],set(),"fixture")[0]
    rules=[verify._build_rule("caller",False,False,[])]
    assert verify._scan_lines(rows[:4],rules,set(),"fixture")[0]


def test_log_frame_supplies_context_for_split_public_namespace():
    rows=[verify.OcrLine("INFO",[10,y,40,14]) for y in [20,50,80]]
    rows+=[verify.OcrLine("addons.base.models.ir",[700,80,200,14]),
           verify.OcrLine("customer.example",[700,120,200,14])]
    found,boxes=verify._scan_lines(rows,[],set(),"fixture")
    assert len(found)==1 and boxes==[rows[-1].box]
    assert verify._scan_lines(rows[2:],[],set(),"fixture")[0]


def test_variable_header_survives_block_order_and_neighboring_caption():
    rows=[verify.OcrLine("System",[850,104,55,14]),verify.OcrLine("Variables",[910,100,70,20]),
          verify.OcrLine("Folder Variables",[720,100,115,14]),
          verify.OcrLine("caller.id",[909,140,70,14])]
    assert not verify._scan_lines(rows,[],set(),"fixture")[0]
    assert verify._scan_lines(rows[-1:],[],set(),"fixture")[0]


def test_variable_ocr_can_omit_a_dot_only_in_the_typed_column():
    rows=[verify.OcrLine("System Variables",[850,100,130,14]),
          verify.OcrLine("systemcaller.id",[850,140,130,14]),
          verify.OcrLine("systemcaller.id",[100,140,130,14]),
          verify.OcrLine("customer.example",[850,180,130,14])]
    found,boxes=verify._scan_lines(rows,[],set(),"fixture")
    assert len(found)==2 and rows[2].box in boxes and rows[3].box in boxes


def test_damaged_public_variable_glyphs_need_exact_typed_header():
    rows=[verify.OcrLine("System Variab les",[850,100,140,24]),
          verify.OcrLine("ca11er.id",[850,150,100,14]),
          verify.OcrLine("customer.id",[850,190,100,14]),
          verify.OcrLine("ca11er.id",[100,150,100,14]),
          verify.OcrLine("https://ca11er.id",[850,230,140,14])]
    found,boxes=verify._scan_lines(rows,[],set(),"fixture")
    assert len(found)==3 and rows[1].box not in boxes
    assert all(l.box in boxes for l in rows[2:])
    assert verify._scan_lines(rows[1:2],[],set(),"fixture")[0]
    assert verify._scan_lines(rows[:2],[verify._build_rule("ca11er",False,False,[])],set(),"fixture")[0]


def test_raw_xml_code_quotes_are_local_to_word_text_elements():
    xml = ('<w:p><w:r><w:t>Owner&#39;s procedure</w:t></w:r></w:p>'
           '<w:p w:rsidR="123"><w:r><w:t>ir_cron.id</w:t></w:r></w:p>'
           '<w:p><w:r><w:t>Another owner&#39;s note</w:t></w:r></w:p>')
    assert not verify._scan_part("word/document.xml", xml, [], set())
    for value in ("'ir_cron.id'", "https://ir_cron.id", "customer.example"):
        bad = '<w:p><w:r><w:t>' + value + '</w:t></w:r></w:p>'
        assert verify._scan_part("word/document.xml", bad, [], set())
    attribute = '<w:p w:val="ir_cron.id"><w:r><w:t>Public label</w:t></w:r></w:p>'
    assert any("(raw)" in f for f in verify._scan_part("word/document.xml", attribute, [], set()))
    rules = [verify._build_rule("ir_cron", False, True, [])]
    assert verify._scan_part("word/document.xml", xml, rules, set())


def test_drawing_offsets_do_not_join_into_identifiers():
    def anchor(h, v):
        return ('<w:r><w:drawing><wp:anchor><wp:positionH relativeFrom="column">'
                f'<wp:posOffset>{h}</wp:posOffset></wp:positionH><wp:positionV relativeFrom="paragraph">'
                f'<wp:posOffset>{v}</wp:posOffset></wp:positionV></wp:anchor></w:drawing></w:r>')
    xml = ('<w:p>' + anchor(4386580, 863601) + anchor(2993390, 985012)
           + '<w:r><w:t>Step 3</w:t></w:r>' + anchor("-12345678", 1752600) + '</w:p>'
           + '<w:p><w:r><wp:align>left</wp:align><w:t>Version 2</w:t></w:r></w:p>')
    assert not verify._scan_part("word/document.xml", xml, [], set())
    leak = xml.replace("Step 3", "Account 4386580863601")
    assert any("(text)" in f and "identifier" in f for f in verify._scan_part("word/document.xml", leak, [], set()))
