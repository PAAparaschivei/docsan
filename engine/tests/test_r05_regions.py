"""Synthetic code/footer coverage and adjacent-content preservation."""
import random

import imagekit
from docsan.image.helper import call
from docsan.image.regions import footer_masks, visual_masks
from docsan.image.text import ImageText, Line, Mask, Reading, _overlap, fit_to_ink


def coverage(mask, target):
    x=max(0,min(mask[0]+mask[2],target[0]+target[2])-max(mask[0],target[0]))
    y=max(0,min(mask[1]+mask[3],target[1]+target[3])-max(mask[1],target[1]))
    return x*y/(target[2]*target[3])


def line(value,box):
    x,y,w,h=box
    return Line(box,[Reading(value,1,[(x+i*w/len(value),y,w/len(value),h) for i in range(len(value))])],0,True)


def test_undecodable_matrix_geometry_is_detected_without_payload(tmp_path):
    imagekit.helper()
    source=tmp_path/'damaged-code.png'
    rng=random.Random(19)
    items=[]
    for y in range(19):
        for x in range(19):
            if rng.random()<.48:
                items.append({'shape':'rect','box':[90+x*2,80+y*2,2,2],'color':[.55,.55,.55]})
    items += [{'text':'Amount 450 EUR','x':180,'y':80,'size':20},
              {'shape':'ellipse','box':[320,150,40,40],'stroke':3}]
    imagekit.render({'output':str(source),'width':600,'height':400,'items':items})
    observed=call({'command':'regions','input':str(source)})
    assert observed['barcodes']==[]  # damaged matrix has no decodable content
    assert any(coverage(tuple(b),(90,80,38,38))>.9 for b in observed['code_candidates'])
    masks=visual_masks(source,ImageText(600,400,1,1,False,[]),[])
    assert any(m.label=='CODE' and m.source=='matrix' for m in masks)
    for m in masks:
        assert _overlap(m.box,(180,80,150,24))==0
        assert _overlap(m.box,(320,150,40,40))==0
    masked=tmp_path/'covered.png'
    call({'command':'mask','input':str(source),'output':str(masked),'format':'png','boxes':[list(m.box) for m in masks]})
    assert call({'command':'regions','input':str(masked)})['code_candidates']==[]


def test_footer_grows_past_clipped_baseline_without_invoice_row():
    body=line('Invoice amount 1234 EUR',(30,900,500,20))
    footer=line('Aurelan Ltd 23 Rue des Pins',(30,950,560,10))
    page=line('Page 13',(900,980,60,12))
    text=ImageText(1000,1000,1,1,False,[body,footer,page])
    seeds=[Mask((30,950,80,10),'CLIENT','text')]
    masks=footer_masks(text,seeds)
    assert len(masks)==1
    assert _overlap(masks[0].box,(30,950,560,27))>.99
    assert _overlap(masks[0].box,body.box)==0
    assert _overlap(masks[0].box,page.box)==0
    assert footer_masks(text,[])==[]
    # Body-sized role text at the bottom must remain a text-level mask.
    text.lines=[line('Aurelan service owner',(30,920,600,40))]
    assert footer_masks(text,seeds)==[]


def test_fit_trims_empty_line_height_without_touching_adjacent_word(tmp_path):
    imagekit.helper()
    path=tmp_path/'roles.png'
    words=imagekit.render({'output':str(path),'width':700,'height':300,'items':[
        {'text':'Aurelan','x':100,'y':100,'size':28},
        {'text':'Service owner','x':240,'y':100,'size':28}]})
    word=words[0]['box'];x,y,w,h=word
    found=fit_to_ink(path,[Mask((x,y-8,w,h+16),'ORG','text',reach=6)],700,300)
    assert len(found)==1 and found[0].box[3] < h+12
    assert coverage(found[0].box,tuple(word))>.99
    assert all(_overlap(found[0].box,tuple(v['box']))==0 for v in words[1:])


def test_small_invoice_row_is_not_expanded_as_footer():
    row=line('Customer Seabright Total 450 EUR',(30,950,600,10))
    text=ImageText(1000,1000,1,1,False,[row])
    assert footer_masks(text,[Mask((130,950,90,10),'CLIENT','text')])==[]


def test_footer_fragments_join_the_sensitive_legal_block():
    rows=[line('Aurelan Ltd',(25,940,310,8)),line('clipped legal continuation',(30,955,500,7)),
          line('1234 EUR',(780,955,170,10)),line('Page 13',(900,985,60,10))]
    text=ImageText(1000,1000,1,1,False,rows)
    masks=footer_masks(text,[Mask((25,940,90,8),'CLIENT','text')])
    assert len(masks)==1 and coverage(masks[0].box,(30,955,500,20))>.99
    assert _overlap(masks[0].box,rows[2].box)==0 and _overlap(masks[0].box,rows[3].box)==0


def test_word_guard_stops_loose_box_before_neighboring_business_text(tmp_path):
    imagekit.helper()
    path=tmp_path/'loose-word.png'
    words=imagekit.render({'output':str(path),'width':700,'height':250,'items':[
        {'text':'Aurelan','x':100,'y':100,'size':28},
        {'text':'Business','x':215,'y':100,'size':28}]})
    x,y,_w,h=words[0]['box']; other=tuple(words[1]['box'])
    loose=(x,y-4,other[0]-x+8,h+8)
    guard=(other[0]+8,other[1],other[2],other[3])
    masks=fit_to_ink(path,[Mask(loose,'PERSON','text',reach=20,guards=(guard,))],700,250)
    assert coverage(masks[0].box,tuple(words[0]['box']))>.99
    assert _overlap(masks[0].box,other)==0


def test_matrix_with_gray_background_is_still_detected(tmp_path):
    imagekit.helper()
    rng=random.Random(19);path=tmp_path/'blurred-gray-code.png'
    items=[{'shape':'rect','box':[90,80,38,38],'color':[.75,.75,.75]}]
    items += [{'shape':'rect','box':[90+x*2,80+y*2,2,2],'color':[.25,.25,.25]}
              for y in range(19) for x in range(19) if rng.random()<.48]
    imagekit.render({'output':str(path),'width':400,'height':240,'items':items})
    result=call({'command':'regions','input':str(path)})
    assert any(coverage(tuple(b),(90,80,38,38))>.9 for b in result['code_candidates'])


def test_email_box_swallowing_next_word_stops_at_ink_gap(tmp_path):
    imagekit.helper()
    path=tmp_path/'email-neighbor.png'
    words=imagekit.render({'output':str(path),'width':1000,'height':200,'items':[
        {'text':'elena.marchetti@aurelan.lu now.','x':60,'y':60,'size':28}]})
    email=tuple(words[0]['box']);neighbor=tuple(words[1]['box'])
    x,y,w,h=email
    oversized=(x,y-3,neighbor[0]+neighbor[2]-x+2,h+6)
    masks=fit_to_ink(path,[Mask(oversized,'EMAIL','text',reach=6)],1000,200,
                     guards=[neighbor])
    assert len(masks)==1 and coverage(masks[0].box,email)>.99
    assert _overlap(masks[0].box,neighbor)==0


def test_colored_decorative_mosaic_is_not_a_grayscale_code(tmp_path):
    imagekit.helper()
    rng=random.Random(19);path=tmp_path/'decorative-mosaic.png'
    items=[{'shape':'rect','box':[90+x*2,80+y*2,2,2],'color':[.2,.3,.35]}
           for y in range(19) for x in range(19) if rng.random()<.48]
    imagekit.render({'output':str(path),'width':400,'height':240,'items':items})
    assert call({'command':'regions','input':str(path)})['code_candidates']==[]
