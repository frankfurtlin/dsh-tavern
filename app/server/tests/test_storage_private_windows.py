import ctypes,os
from pathlib import Path
import pytest
from app import storage
from app.errors import Problem


def collision():
    error=PermissionError('sharing violation');error.winerror=32;return error


def test_received_journal_survives_bounded_windows_sharing_collisions(tmp_path,monkeypatch):
    monkeypatch.setattr(storage.settings,'storage_path',tmp_path)
    storage.write_private('owner','receipt',b'old-complete-receipt')
    replace=storage.os.replace;attempts=[]
    def busy(source,target):
        attempts.append(1)
        assert Path(target).read_bytes()==b'old-complete-receipt'
        if len(attempts)<3:raise collision()
        return replace(source,target)
    monkeypatch.setattr(storage.os,'replace',busy)
    storage.write_private('owner','receipt',b'new-complete-receipt')
    assert len(attempts)==3 and (tmp_path/'owner/receipt').read_bytes()==b'new-complete-receipt'
    assert not list((tmp_path/'owner').glob('*.tmp'))


def test_persistent_sharing_error_is_finite_and_preserves_the_previous_receipt(tmp_path,monkeypatch):
    monkeypatch.setattr(storage.settings,'storage_path',tmp_path)
    storage.write_private('owner','receipt',b'original');attempts=[]
    def busy(*args):attempts.append(1);raise collision()
    monkeypatch.setattr(storage.os,'replace',busy)
    with pytest.raises(PermissionError):storage.write_private('owner','receipt',b'new')
    assert len(attempts)==6 and (tmp_path/'owner/receipt').read_bytes()==b'original'
    assert not list((tmp_path/'owner').glob('*.tmp'))


def test_deletion_during_sharing_retry_never_revives_a_receipt(tmp_path,monkeypatch):
    monkeypatch.setattr(storage.settings,'storage_path',tmp_path);attempts=[]
    def busy(*args):
        attempts.append(1);(tmp_path/'.deleted').mkdir();(tmp_path/'.deleted/owner').touch();raise collision()
    monkeypatch.setattr(storage.os,'replace',busy)
    with pytest.raises(Problem,match='账号已清理'):storage.write_private('owner','receipt',b'new')
    assert attempts==[1] and not (tmp_path/'owner/receipt').exists()


@pytest.mark.skipif(os.name!='nt',reason='Actual Windows sharing semantics')
def test_actual_windows_reader_handle_can_close_before_atomic_journal_replacement(tmp_path,monkeypatch):
    from ctypes import wintypes
    monkeypatch.setattr(storage.settings,'storage_path',tmp_path)
    storage.write_private('owner','receipt',b'old')
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    create=kernel.CreateFileW;create.restype=wintypes.HANDLE
    create.argtypes=[wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,ctypes.c_void_p,wintypes.DWORD,wintypes.DWORD,wintypes.HANDLE]
    kernel.CloseHandle.argtypes=[wintypes.HANDLE]
    handle=create(str(tmp_path/'owner/receipt'),0x80000000,3,None,3,0,None)
    assert handle!=wintypes.HANDLE(-1).value,ctypes.get_last_error()
    replace=storage.os.replace;collisions=[];closed=False
    def held(source,target):
        nonlocal closed
        try:return replace(source,target)
        except PermissionError:
            collisions.append(1)
            if len(collisions)==2:
                assert kernel.CloseHandle(handle);closed=True
            raise
    monkeypatch.setattr(storage.os,'replace',held)
    try:storage.write_private('owner','receipt',b'new')
    finally:
        if not closed:kernel.CloseHandle(handle)
    assert len(collisions)==2 and (tmp_path/'owner/receipt').read_bytes()==b'new'
