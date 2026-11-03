"""Small raster text is checked at readable resolution, without exempting IDs."""
import imagekit
import leakmatrix as lm
from docsan import verify
from docsan.image.encode import write_png
from pngio import read_png, without_alpha


def picture(tmp_path, text):
    imagekit.helper()
    raw=tmp_path/'source.png';out=tmp_path/'release.png'
    imagekit.render({'output':str(raw),'width':700,'height':80,'items':[
        {'text':text,'x':12,'y':25,'size':9,'font':'Arial'}]})
    px=without_alpha(read_png(raw.read_bytes()))
    out.write_bytes(write_png(px.data,px.width,px.height,False))
    return out


def test_tiny_calendar_values_remain_clear(tmp_path):
    path=picture(tmp_path,'Created on is between 01/07/2022 00:00:00 and 11/07/2022 23:59:59')
    result=verify.verify_image(path,lm.PROFILE)
    assert result.status=='pass', (result.findings,result.problems)


def test_tiny_sensitive_name_and_account_still_block(tmp_path):
    path=picture(tmp_path,'Customer Aurelan account 5478630219')
    result=verify.verify_image(path,lm.PROFILE)
    assert result.status=='findings'
    assert any('term' in f for f in result.findings)
    assert any('identifier' in f for f in result.findings)
    assert result.regions and all(0<=b[0]<=700 and 0<=b[1]<=80 for b in result.regions)


def test_unavailable_zoom_blocks_inspection(tmp_path,monkeypatch):
    path=picture(tmp_path,'Integration overview')
    def unavailable(*args):
        raise verify._Uninspectable('cannot enlarge image')
    monkeypatch.setattr(verify,'_zoomed',unavailable)
    assert verify.verify_image(path,lm.PROFILE).status=='could_not_inspect'


def test_tiny_flat_patch_is_not_sensitive_text():
    pixels = bytearray([223, 228, 235] * 20 * 20)
    for y in range(9, 12):
        for x in range(9, 12):
            i = (y * 20 + x) * 3
            pixels[i:i + 3] = bytes([222, 227, 234])
    picture = verify._Picture(20, 20, channels=3, pixels=bytes(pixels))
    line = verify.OcrLine('AB', [18, 18, 6, 6])
    rule = verify._build_rule('AB', True, True, [])
    assert verify._scan_lines([line], [rule], set(), 'fixture', picture, 2) == ([], [])


def test_small_real_letters_keep_the_finding():
    pixels = bytearray([223, 228, 235] * 20 * 20)
    # Even a faint stroke (three grey levels) is not treated as a flat patch.
    i = (10 * 20 + 10) * 3
    pixels[i:i + 3] = bytes([220, 225, 232])
    picture = verify._Picture(20, 20, channels=3, pixels=bytes(pixels))
    line = verify.OcrLine('AB', [18, 18, 6, 6])
    rule = verify._build_rule('AB', True, True, [])
    assert verify._scan_lines([line], [rule], set(), 'fixture', picture, 2)[0]
    assert verify._scan_lines([line], [rule], set(), 'fixture')[0]


def test_flat_patch_filter_requires_valid_opaque_pixels():
    line = verify.OcrLine('AB', [18, 18, 6, 6])
    picture = verify._Picture(20, 20, channels=4, pixels=bytes([223, 228, 235, 0] * 20 * 20))
    assert not verify._flat_ocr_artifact(line, picture, 2)
    assert not verify._flat_ocr_artifact(verify.OcrLine('AB', [0, 0, 6, 6]), picture, 2)
