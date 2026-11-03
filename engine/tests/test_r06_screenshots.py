"""Synthetic identity-field coverage with neighboring content assertions."""
from docsan import verify
from docsan.image.regions import field_masks, screenshot_masks, visual_masks
from docsan.image.text import ImageText, Mask, _overlap
from docsan.match import Matcher
from docsan.profile import Profile
from test_r05_regions import coverage, line


def test_network_addresses_and_contextual_hosts_have_independent_checks():
    matcher=Matcher(Profile("Example","v1",[]),set())
    for value in ("IP address 192.0.2.47", "10.2.3.4", "Serveur EXAMPLEDBP01", "Snapshots EXAMPLEP01 - EXAMPLEDBP01"):
        assert matcher.find(value).hits
        assert verify._pattern_findings(value.lower(),set(),True)
    for value in ("version 10.2.3.4", "release 1.2.3.4", "SDK 999.12.3.4", "Server PostgreSQL13", "Amount 1234.50 EUR"):
        assert not matcher.find(value).hits
        assert not verify._pattern_findings(value.lower(),set(),True)
    assert matcher.find("sender@aurelan.l",loose_case=True).hits


def test_bookmark_strip_requires_top_address_and_wide_row():
    rows=[line("https://portal.aurelan.example",(150,20,550,16)),
          line("Enterprise tools",(10,56,180,14)),line("Mail",(400,56,80,14)),line("Projects",(900,56,120,14)),
          line("Scheduled actions",(10,110,300,24)),line("Amount 450 EUR",(20,220,260,20))]
    source=ImageText(1300,800,1,1,False,rows)
    masks=screenshot_masks(source)
    assert len(masks)==1 and coverage(masks[0].box,(0,0,1300,70))>.99
    assert all(_overlap(masks[0].box,l.box)==0 for l in rows[-2:])
    assert screenshot_masks(ImageText(1300,800,1,1,False,rows[1:]))==[]
    assert screenshot_masks(ImageText(1300,800,1,1,False,[rows[0],rows[1],*rows[-2:]]))==[]


def test_reply_header_masks_recipients_and_avatar_without_subject_or_body():
    rows=[line("Monthly collection",(20,5,350,18)),line("Reply",(720,55,70,12)),
          line("Reply All",(820,55,85,12)),line("Forward",(930,55,80,12)),
          line("To",(80,82,15,12)),line("Elena Marchetti",(110,100,150,12)),line("Message body",(10,190,500,16))]
    masks=screenshot_masks(ImageText(1100,700,1,1,False,rows))
    assert len(masks)==1 and coverage(masks[0].box,(0,52,1100,61))>.99
    assert _overlap(masks[0].box,rows[0].box)==0 and _overlap(masks[0].box,rows[-1].box)==0


def test_comment_header_masks_avatar_without_comment_text():
    rows=[line("Elena Marchetti",(65,18,200,18)),line("Publié le 12 déc. 2023",(65,44,260,12)),
          line("Bonjour",(65,76,120,18)),line("Preserve the support explanation",(65,120,700,20))]
    masks=screenshot_masks(ImageText(1000,500,1,1,False,rows))
    assert coverage(masks[0].box,(5,5,45,49))>.99
    assert all(_overlap(masks[0].box,l.box)==0 for l in rows[2:])


def test_assignee_column_covers_photo_and_account_but_keeps_ticket_context():
    rows=[line("Nom",(260,208,50,16)),line("Assignés",(650,208,80,16)),line("Temps passé",(1100,208,120,16)),
          line("Scheduled job issue",(260,270,290,18)),line("Elena Marchetti (elma)",(650,270,240,18)),
          line("00:00",(1100,270,50,18))]
    masks=screenshot_masks(ImageText(1350,420,1,1,False,rows))
    assert len(masks)==1 and coverage(masks[0].box,(654,265,240,30))>.99
    assert _overlap(masks[0].box,rows[-3].box)==0 and _overlap(masks[0].box,rows[-1].box)==0


def test_network_table_masks_all_user_columns_and_preserves_protocol_row():
    rows=[line("User",(25,330,30,14)),line("elena",(125,330,100,14)),line("marchetti",(310,330,100,14)),
          line("Protocol",(25,285,60,14)),line("SFTP",(310,285,60,14))]
    masks=screenshot_masks(ImageText(850,700,1,1,False,rows))
    assert len(masks)==1 and all(coverage(masks[0].box,l.box)>.99 for l in rows[1:3])
    assert _overlap(masks[0].box,rows[0].box)==0 and all(_overlap(masks[0].box,l.box)==0 for l in rows[3:])


def test_subscription_owner_is_person_field_without_financial_mask():
    rows=[line("Subscription owner",(540,460,155,14)),line("Elena Marchetti",(720,460,130,14)),
          line("Start date",(540,430,85,14)),line("01/06/2024",(720,430,110,14))]
    masks=field_masks(ImageText(1100,650,1,1,False,rows))
    assert len(masks)==1 and masks[0].label=="PERSON" and coverage(masks[0].box,rows[1].box)>.99
    assert all(_overlap(masks[0].box,l.box)==0 for l in (rows[0],*rows[2:]))


def test_brand_icon_is_covered_when_connected_to_full_width_header(monkeypatch,tmp_path):
    monkeypatch.setattr("docsan.image.regions.call",lambda request:{"barcodes":[],"code_candidates":[],"colour_components":[[0,0,1800,60]]})
    seed=Mask((80,20,130,18),"ORG","text")
    rows=[line("Aurelan portal",seed.box),line("Files",(280,20,60,18))]
    masks=visual_masks(tmp_path/"screen.png",ImageText(1800,800,1,1,False,rows),[seed])
    assert any(coverage(m.box,(35,6,40,40))>.99 for m in masks)
    assert all(_overlap(m.box,rows[1].box)==0 for m in masks)
    # A real left-hand field label is protected from the brand expansion.
    rows.insert(0,line("Customer:",(5,20,70,18)))
    assert visual_masks(tmp_path/"screen.png",ImageText(1800,800,1,1,False,rows),[seed])==[]


def test_reply_toolbar_with_leading_icons_and_brand_with_neighbors(monkeypatch,tmp_path):
    rows=[line("↩ Reply",(720,50,70,18)),line("↩ Reply All",(810,50,85,18)),line("→ Forward",(920,50,80,14)),
          line("Monthly collection",(20,20,350,22)),line("Body remains",(20,190,300,20))]
    masks=screenshot_masks(ImageText(1100,700,1,1,False,rows))
    assert len(masks)==1 and all(_overlap(masks[0].box,l.box)==0 for l in rows[3:])
    monkeypatch.setattr("docsan.image.regions.call",lambda request:{"barcodes":[],"code_candidates":[],"colour_components":[[0,70,1800,55]]})
    rows=[line("Bookmarks",(0,43,150,17)),line("Seabright files",(76,82,138,21)),line("Files",(0,140,60,14))]
    masks=visual_masks(tmp_path/"screen.png",ImageText(1800,800,1,1,False,rows),[Mask(rows[1].box,"CLIENT","text")])
    assert any(coverage(m.box,(35,75,40,40))>.99 for m in masks)
    assert all(_overlap(m.box,l.box)==0 for m in masks for l in (rows[0],rows[2]))


def test_short_table_row_does_not_gain_a_corner_brand_mask(monkeypatch,tmp_path):
    monkeypatch.setattr("docsan.image.regions.call",lambda request:{"barcodes":[],"code_candidates":[],"colour_components":[[0,0,1800,60]]})
    rows=[line("5 Aurelan TEL: Export",(120,5,400,14))]
    seed=Mask((145,5,65,14),"ORG","text")
    assert visual_masks(tmp_path/"row.png",ImageText(1800,150,1,1,False,rows),[seed])==[]


def test_corner_brand_preserves_words_sharing_the_sensitive_ocr_line(monkeypatch,tmp_path):
    monkeypatch.setattr("docsan.image.regions.call",lambda request:{"barcodes":[],"code_candidates":[],"colour_components":[[0,0,1800,60]]})
    rows=[line("Scheduled actions Aurelan TEL: Import",(10,5,500,16))]
    seed=Mask((165,5,65,16),"ORG","text",guards=((10,5,150,16),))
    assert visual_masks(tmp_path/"screen.png",ImageText(1800,800,1,1,False,rows),[seed])==[]


def test_reply_toolbar_can_be_one_ocr_line():
    rows=[line("↩ Reply ↩ Reply All → Forward",(700,50,330,18)),
          line("Subject stays",(20,15,400,20)),line("Private recipient",(80,82,300,16)),
          line("Message body stays",(20,190,500,20))]
    masks=screenshot_masks(ImageText(1100,600,1,1,False,rows))
    assert len(masks)==1 and coverage(masks[0].box,rows[2].box)>.99
    assert _overlap(masks[0].box,rows[1].box)==0 and _overlap(masks[0].box,rows[3].box)==0
