"""Deterministic screenshot fields and brand-adjacent geometry.

These are local document policies, not a general company-anonymity claim.
Labels and numeric amounts stay; account/customer identifiers and address
values do not. The final verifier still reads the emitted PDF independently.
"""
from __future__ import annotations

import re

from .helper import call
from .text import (
    ImageText,
    Mask,
    _merge,
    _overlap,
    _span_box,
    _union,
    in_system_column,
    system_variable_headers,
    unmasked,
    whole_pixels,
)

_ADDRESS = re.compile(r"(?:^|\s)\d{1,4}\s*,?\s*(?:rue|boulevard|bd\.?|avenue|av\.?|street|road)\b|\bL[- ]?\d{4}\b", re.IGNORECASE)
_LEGAL = re.compile(r"\b(?:s\.?\s*a\.?|s\.?a\.?r\.?l\.?|ltd\.?|gmbh|inc\.?)\s*$", re.IGNORECASE)
_FIELDS = re.compile(r"^(?:customer|client|account name|titulaire du compte|salesperson|vendeur|subscription owner|propriétaire de souscription|invoice address|adresse de facturation)\s*:?\s*$", re.IGNORECASE)
_INLINE = re.compile(r"^(?:customer|client|account name|titulaire du compte|salesperson|vendeur)\s*:\s*(\S.*)$", re.IGNORECASE)

def field_masks(text: ImageText, lines=None) -> list[Mask]:
    lines = text.best() if lines is None else lines
    masks: list[Mask] = []
    def add(line, label):
        masks.append(Mask(whole_pixels(line.box, text.width, text.height, (2, 2)), label, "field"))
    for line in lines:
        value = line.readings[0].text.strip()
        if m := _INLINE.match(value):
            r = line.readings[0]
            box = _span_box(line, r, m.start(1), m.end(1))
            masks.append(Mask(whole_pixels(box, text.width, text.height, (2, 2)), "CLIENT", "field"))
        elif _ADDRESS.search(value):
            add(line, "ADDRESS")
        elif (_LEGAL.search(value) or re.search(r"\bS\.A\.", value)) and len(re.sub(r"\W", "", value)) >= 7:
            add(line, "CLIENT")
        elif _FIELDS.fullmatch(value):
            x, y, w, h = line.box
            # A value on the same row must be the closest text to the right.
            right = [other for other in lines if other is not line
                     and abs((other.box[1] + other.box[3]/2) - (y+h/2)) < max(h, other.box[3])*.55
                     and 0 <= other.box[0] - x - w < min(text.width*.16, 14*h)]
            if right:
                other = min(right, key=lambda l: l.box[0])
                if not _FIELDS.fullmatch(other.readings[0].text.strip()):
                    add(other, "PERSON" if value.lower() in ("salesperson", "vendeur", "subscription owner", "propriétaire de souscription") else "CLIENT")
    form_labels = [line for line in lines if re.fullmatch(
        r"(?:order date|date de commande|subscription|souscription|product|produit|customer|client|account number)",
        line.readings[0].text.strip().rstrip(":"), re.IGNORECASE)]
    for line in lines:
        if not re.fullmatch(r"[0-9]{5,7}", line.readings[0].text.strip()):
            continue
        x, y, w, h = line.box
        nearby = [label for label in form_labels if 0 < label.box[1]-y < 8*h
                  and abs(label.box[0]-x) < text.width*.35 and h > 1.35*label.box[3]]
        if len(nearby) >= 2:
            add(line, "ID")
    masks += screenshot_masks(text, lines)
    return masks

def visual_masks(path, text: ImageText, seeds: list[Mask], lines=None, scale: float = 1.0, crop=None) -> list[Mask]:
    lines = text.best() if lines is None else lines
    request = {"command": "regions", "input": str(path)}
    if crop and any(crop):
        request["crop"] = list(crop)
    raw = call(request)
    # Helper responses are cached. Scaling must not change the cached geometry.
    observed = {name: [[v * scale for v in box] for box in raw[name]]
                for name in ("barcodes", "colour_components")}
    candidates = [[v * scale for v in box] for box in raw["code_candidates"]]
    masks = [Mask(whole_pixels(tuple(b), text.width, text.height, (4, 4)), "CODE", "barcode")
             for b in observed["barcodes"]]
    for b in candidates:
        if not any(_overlap(tuple(b), m.box) > .5 for m in masks):
            masks.append(Mask(whole_pixels(tuple(b), text.width, text.height, (2, 2)), "CODE", "matrix"))
    for seed in seeds:
        x, y, w, h = seed.box
        if seed.label not in ("ORG", "CLIENT") or not (x < text.width*.25 and y < text.height*.2):
            continue
        left = max(0, x-6*h)
        top,bottom=max(0,y-2*h),min(text.height,y+3*h)
        for l in lines:
            lx,ly,lw,lh=l.box
            if _overlap(l.box,seed.box)>.1 or min(x+w,lx+lw)<=max(left,lx):
                continue
            if ly+lh<=y:
                top=max(top,(ly+lh+y)/2)
            elif ly>=y+h:
                bottom=min(bottom,(y+h+ly)/2)
        adjacent = (left,top,x+w-left,max(0,bottom-top))
        colour_nearby=any(_overlap(tuple(b),adjacent)>.15 for b in observed["colour_components"])
        if text.height>=300 and x>0 and colour_nearby and not any(
                _overlap(b,seed.box)<.1 and _overlap(b,adjacent)>.15 for b in seed.guards) and not any(
                _overlap(l.box,seed.box)<.1 and _overlap(l.box,adjacent)>.15 for l in lines):
            masks.append(Mask(whole_pixels(adjacent,text.width,text.height,(2,2)),"LOGO","brand"))
        components = []
        for b in observed["colour_components"]:
            bx, by, bw, bh = b
            dx = max(x-bx-bw, bx-x-w, 0)
            dy = max(y-by-bh, by-y-h, 0)
            if (bw < text.width*.12 and bh < text.height*.18 and bw*bh >= h*h*.25
                    and dx <= max(3*h, text.width*.02) and dy <= 2*h):
                components.append(tuple(b))
        if components:
            region = _union([seed.box, *components])
            # Avoid expanding a brand mask into unrelated nearby text.
            neighbors = [line.box for line in lines if _overlap(line.box, seed.box) < .1
                         and _overlap(line.box, region) > .15]
            if not neighbors:
                masks.append(Mask(whole_pixels(region, text.width, text.height, (3, 3)), "LOGO", "brand"))
    return masks


def footer_masks(text: ImageText, seeds: list[Mask], lines=None) -> list[Mask]:
    """Cover connected sensitive small-print footer blocks, including fragments.

    Geometry is limited to a column and stops before larger body rows. Financial
    rows cannot seed or join a footer unless they contain legal/address cues.
    """
    lines = text.best() if lines is None else lines
    eligible=[]
    for line in lines:
        if any(m.source == "context" and _overlap(line.box,m.box) > .15 for m in seeds):
            continue  # a typed scroll-pane continuation is not legal footer text
        x,y,w,h=line.box
        value=line.readings[0].text
        legal=_LEGAL.search(value) or _ADDRESS.search(value) or re.search(
            r"\b(?:capital|registre|registered|registration|TVA|VAT|IBAN|BIC|RCS)\b",value,re.IGNORECASE)
        financial=re.search(r"[€$£]|\b(?:EUR|USD|GBP|amount|montant|total|price|prix)\b",value,re.IGNORECASE)
        if y>=text.height*.90 and 2<=h<=text.height*.018 and (legal or not financial):
            eligible.append(line)
    remaining=list(eligible); result=[]
    while remaining:
        first=remaining.pop(0); group=[first]; box=first.box
        changed=True
        while changed:
            changed=False
            for other in remaining[:]:
                ox,oy,ow,oh=other.box
                x,y,w,h=box
                typical=max(l.box[3] for l in group)
                dx=max(x-ox-ow,ox-x-w,0);dy=max(y-oy-oh,oy-y-h,0)
                merged=_union([box,other.box])
                if dx<=8*max(typical,oh) and dy<=4*max(typical,oh) and merged[2]<=text.width*.60:
                    group.append(other);remaining.remove(other);box=merged;changed=True
        hits=[m for m in seeds if m.source not in ("owner","footer")
              and m.label in ("ORG","CLIENT","ADDRESS","SITE","PHONE","EMAIL","DOMAIN","URL","ID")
              and any(_overlap(m.box,l.box)>.15 for l in group)]
        if not hits:
            continue
        x,y,w,h=box;typical=max(l.box[3] for l in group)
        top,bottom=y-.6*typical,y+h+3*typical
        for other in lines:
            if any(other is l for l in group):
                continue
            ox,oy,ow,oh=other.box
            if min(x+w,ox+ow)<=max(x,ox):
                continue
            if oy+oh<=y:
                top=max(top,(oy+oh+y)/2)
            if oy>=y+h and oh>typical*1.4:
                bottom=min(bottom,(y+h+oy)/2)
        result.append(Mask(whole_pixels((x-2*typical,top,w+4*typical,max(0,bottom-top)),
                                       text.width,text.height,(1,1)),"FOOTER","footer"))
    return result


def screenshot_masks(text: ImageText, lines=None) -> list[Mask]:
    """Small UI identity fields, bounded by independently read layout cues."""
    lines = text.best() if lines is None else lines
    result=[]
    values=[(l,l.readings[0].text.strip()) for l in lines]
    def cover(box,label):
        result.append(Mask(whole_pixels(box,text.width,text.height,(3,3)),label,"field"))
    # A browser bookmark row lies just below a top address line. Hide the whole
    # chrome strip, including unreadable favicons/profile pictures; keep the app.
    urls=[l for l,v in values if re.search(r"(?:https?://|www\.)",v,re.IGNORECASE)
          and l.box[1]<text.height*.12 and l.box[0]<text.width*.4]
    for address in urls:
        x,y,w,h=address.box
        row=[l for l,v in values if y+h<l.box[1]<y+7*h and l.box[3]<=h*1.8]
        for anchor in row:
            peers=[l for l in row if abs(l.box[1]-anchor.box[1])<max(h,l.box[3])*.8]
            if len(peers)>=3 and max(l.box[0]+l.box[2] for l in peers)-min(l.box[0] for l in peers)>text.width*.55:
                bottom=max(l.box[1]+l.box[3] for l in peers)+h*.4
                if bottom<text.height*.22:
                    cover((0,0,text.width,bottom),"BROWSER")
                    break
    # Remote-session titles and tabs can contain private connection aliases.
    # The public menu and command rows delimit two bands; keep both rows.
    menus=[l for l,v in values if l.box[1]<text.height*.08
           and re.search(r"\b(?:sessions|tools|settings|macros)\b",v,re.IGNORECASE)]
    commands=[l for l,v in values if l.box[1]<text.height*.15
              and re.search(r"\b(?:detach tab|set connection password|disconnect|fullscreen)\b",v,re.IGNORECASE)]
    menu_cues={m.lower() for l in menus for m in re.findall(r"\b(?:sessions|tools|settings|macros)\b",l.readings[0].text,re.IGNORECASE)}
    command_cues={m.lower() for l in commands for m in re.findall(r"\b(?:detach tab|set connection password|disconnect|fullscreen)\b",l.readings[0].text,re.IGNORECASE)}
    if (len(menu_cues)>=2 and len(command_cues)>=2) or (menu_cues and len(command_cues)>=3):
        top=min(l.box[1] for l in menus)
        bottom=max(l.box[1]+l.box[3] for l in menus)
        toolbar=min(l.box[1] for l in commands)
        if 0<top<bottom<toolbar<text.height*.15:
            # No padding into the preserved menu/command glyphs.
            result.append(Mask(whole_pixels((0,0,text.width,max(0,top-2)),text.width,text.height),"SESSION","field"))
            if toolbar-bottom>4:
                result.append(Mask(whole_pixels((0,bottom+2,text.width,toolbar-bottom-4),text.width,text.height),"SESSION","field"))
    # Outlook's toolbar is a strong cue: the recipient block sits beside and
    # below Reply/Reply All/Forward. Subject and message body remain readable.
    replies=[l for l,v in values if re.search(r"(?:reply|forward|répondre|transférer)",v,re.IGNORECASE)
             and l.box[0]>text.width*.5 and l.box[1]<text.height*.20]
    reply_tokens=sum(len(re.findall(r"(?:reply|forward|répondre|transférer)",l.readings[0].text,re.IGNORECASE)) for l in replies)
    if replies and reply_tokens>=2:
        h=max(l.box[3] for l in replies); y=min(l.box[1] for l in replies)
        cover((0,max(0,y-3),text.width,5*h+3),"PERSON")
    # Comment headers carry a small avatar and the author immediately above
    # a publication timestamp. Cover that header, preserving the comment body.
    for l,v in values:
        if re.match(r"(?:publié le|published (?:on|at))\b",v,re.IGNORECASE) and l.box[1]<text.height*.35:
            cover((0,0,text.width,l.box[1]+l.box[3]+2),"PERSON")
    # Personal assignees are a table column, including avatars and account IDs.
    for header,v in values:
        if not re.fullmatch(r"(?:assignés?|assignees?|assigned to)",v,re.IGNORECASE):
            continue
        x,y,w,h=header.box
        right=[l for l,_ in values if abs(l.box[1]-y)<h and l.box[0]>x+w]
        if right:
            end=min(l.box[0] for l in right)-h
            left=max(0,x)
            if end>left and end-left<text.width*.55:
                cover((left,y+h+3,end-left,text.height-y-h-3),"PERSON")
    # Network/credential tables may have several value columns on the same row.
    for label,v in values:
        if re.fullmatch(r"(?:ip address|ip adresse|adresse ip|hostname|host name|user|username|utilisateur)",v.rstrip(":"),re.IGNORECASE):
            x,y,w,h=label.box
            if x<text.width*.18:
                cover((x+w+3,max(0,y-3),text.width-x-w-3,h+6),"ID")
    return result


def continuation_masks(text: ImageText) -> list[Mask]:
    """A cropped host row inherits the field type of aligned transport rows."""
    anchors = []
    for line in text.best():
        reading = line.readings[0]
        if m := re.search(r"\bSFTP\b", reading.text, re.IGNORECASE):
            anchors.append(_span_box(line, reading, m.start(), m.end()))
    result = []
    for line in text.lines:
        if line.box[1] < text.height * .85:
            continue
        for reading in line.readings:
            for m in re.finditer(r"(?<![\w])([a-z][a-z0-9_-]{4,}\d{1,3})(?![\w])", reading.text, re.IGNORECASE):
                token = m.group()
                if len(re.sub(r"[^a-z]", "", token.lower())) < 4 or re.fullmatch(
                        r"(?:version|release|postgres(?:ql)?|python|odoo)\d+", token, re.IGNORECASE):
                    continue
                raw = _span_box(line, reading, m.start(), m.end())
                lx,ly,lw,lh = line.box
                x,y = max(raw[0],lx),max(raw[1],ly)
                right,bottom = min(raw[0]+raw[2],lx+lw),min(raw[1]+raw[3],ly+lh)
                if right <= x or bottom <= y:
                    continue
                box = (x,y,right-x,bottom-y)
                h = box[3]
                aligned = [(ax,ay,aw,ah) for ax,ay,aw,ah in anchors
                           if 0 < y-ay < 24*max(h,ah) and -2*ah <= x-ax <= 7*ah]
                if len(aligned) >= 2:
                    result.append(Mask(whole_pixels(box, text.width, text.height, (3,3)), "DOMAIN", "context"))
    return _merge(result)


def _public_label(value: str, public: str, edits: int = 1) -> bool:
    """Bounded OCR spelling check, used only inside a verified public UI role."""
    value=re.sub(r"[^a-z0-9]","",value.lower())
    if abs(len(value)-len(public))>edits:
        return False
    previous=list(range(len(public)+1))
    for i,char in enumerate(value,1):
        current=[i]
        for j,other in enumerate(public,1):
            current.append(min(current[-1]+1,previous[j]+1,previous[j-1]+(char!=other)))
        previous=current
    return previous[-1]<=edits


def _public_ui_label(line, lines, width, height) -> bool:
    value=line.readings[0].text.strip()
    x,y,_w,h=line.box
    values=[(l,l.readings[0].text.strip()) for l in lines]
    menus=[l for l,v in values if l.box[1]<height*.06
           and re.search(r"\b(?:sessions|tools|settings|macros)\b",v,re.IGNORECASE)]
    cues={m.lower() for l in menus for m in re.findall(r"\b(?:sessions|tools|settings|macros)\b",l.readings[0].text,re.IGNORECASE)}
    if len(cues)>=2 and x<width*.15 and y<height*.06 and _public_label(value,"terminal"):
        return any(abs(l.box[1]-y)<max(h,l.box[3]) for l in menus)
    brand=[l for l,v in values if re.search(r"\bgoanywhere\b",v,re.IGNORECASE)]
    library=[l for l,v in values if re.fullmatch(r"component\s+library",v,re.IGNORECASE)]
    if not brand or not library:
        return False
    if value.isupper() and y<height*.30 and x<width*.45 and _public_label(value,"environment"):
        return any(abs(l.box[1]-y)<2*max(h,l.box[3]) and 0<x-l.box[0]-l.box[2]<8*max(h,l.box[3]) for l in brand)
    if _public_label(value,"integration",2):
        return any(ly+lh<y<ly+min(height*.4,40*lh) and lx-2*lh<x<lx+max(lw,18*lh)
                   for lx,ly,lw,lh in (l.box for l in library))
    if _public_label(value,"overwrite",2):
        return any(re.search(r"\bfile\b",v,re.IGNORECASE) and re.search(r"\bexists?\b",v,re.IGNORECASE)
                   and abs(l.box[1]-y)<max(h,l.box[3]) and 0<x-l.box[0]-l.box[2]<8*max(h,l.box[3])
                   for l,v in values)
    return False


def reviewable_lines(text: ImageText, masks: list[Mask], matcher) -> list[str]:
    """Review uses public UI roles; visible/searchable source text is unchanged."""
    headers = system_variable_headers(text)
    lines=text.best()
    return ["" if _public_ui_label(line,lines,text.width,text.height) else
            matcher.reviewable(unmasked(line,masks),
            system_variables=in_system_column(line.box,headers,text.height)) for line in lines]
