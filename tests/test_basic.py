"""Basic tests - verify package can be imported."""

import mutagent
import mutobj


def test_import_mutagent():
    assert hasattr(mutagent, "Declaration")
    assert hasattr(mutagent, "impl")


def test_version():
    assert mutagent.__version__  # 只验证版本号存在，不硬编码具体值


def test_impl_is_mutobj_impl():
    assert mutagent.impl is mutobj.impl
