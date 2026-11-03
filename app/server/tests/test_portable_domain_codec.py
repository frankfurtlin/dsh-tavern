import copy
import hashlib

import pytest

from app.portable.domain_codec import TextPacks, decode_inline, encode_inline, read_text_frame
from app.portable.errors import ArchiveError
from app.portable.remap import validate_private_policy


@pytest.mark.parametrize("field", ["key_cipher", "providerMasterKey", "keyTail"])
def test_actual_operational_key_fields_do_not_enter_portable_documents(field):
    with pytest.raises(ArchiveError, match="BACKUP_PRIVATE_POLICY_INVALID"):
        validate_private_policy({"objects": []}, {"data/object.json": {field: "synthetic-never-real"}})


def test_roundtrip_safe_epub_inline_preserves_unicode_and_formatting():
    markup = '<p>😀 <em>旧信</em><strong>正文</strong><br/><!--comment--><img alt="插图" data-asset="asset-old"/></p>'
    nodes = encode_inline(markup)
    assert decode_inline(nodes) == markup
    assert nodes[0]["children"][-1]["attributes"]["assetId"] == "asset-old"
    nodes[0]["children"][-1]["attributes"]["assetId"] = "asset-new"
    assert 'data-asset="asset-new"' in decode_inline(nodes)
    assert encode_inline(None) is None and decode_inline(None) is None


@pytest.mark.parametrize("markup", ['<script>bad()</script>', '<p onclick="bad()">正文</p>', '<img src="https://evil.example/x"/>', '<iframe>正文</iframe>'])
def test_raw_executable_html_cannot_become_archived_reader_inline(markup):
    with pytest.raises(ArchiveError, match="BACKUP_INLINE_INVALID"): encode_inline(markup)


def test_comment_closure_injection_and_untyped_nodes_are_rejected():
    with pytest.raises(ArchiveError, match="BACKUP_INLINE_INVALID"):
        decode_inline([{"kind": "comment", "value": '--><script>bad()</script><!--'}])
    with pytest.raises(ArchiveError): decode_inline([{"kind": [], "value": "body"}])


def test_prose_packs_preserve_exact_utf8_ranges_and_have_no_per_paragraph_zip_entries(tmp_path):
    packs = TextPacks(tmp_path)
    values = ["😀", "重复段落", "重复段落", "", "尾段。"]
    frames = [packs.write(value) for value in values]; packs.close()
    assert len(packs.entries) == 1
    declared = {entry["path"]: entry for entry in packs.entries}
    assert [read_text_frame(tmp_path, frame, declared) for frame in frames] == values
    assert len({frame["offset"] for frame in frames[:3]}) == 3
    assert packs.entries[0]["sha256"] == hashlib.sha256("".join(values).encode()).hexdigest()


@pytest.mark.parametrize("attack", ["negative", "outside", "wrong-hash", "undeclared", "cut-surrogate"])
def test_untrusted_prose_ranges_do_not_read_outside_declared_files(tmp_path, attack):
    packs = TextPacks(tmp_path); frame = packs.write("😀正文"); packs.close()
    declared = {entry["path"]: entry for entry in packs.entries}
    if attack == "negative": frame["offset"] = -1
    elif attack == "outside": frame["size"] = 100000
    elif attack == "wrong-hash": frame["sha256"] = "0" * 64
    elif attack == "undeclared": frame["path"] = "body/not-in-manifest.txt"
    else:
        frame.update(offset=0, size=1, sha256=hashlib.sha256(b'\xf0').hexdigest())
    with pytest.raises(ArchiveError): read_text_frame(tmp_path, frame, declared)
