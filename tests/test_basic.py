"""Basic tests - verify package can be imported."""

import mutagent
import mutobj


def test_import_mutagent():
    assert hasattr(mutagent, "Declaration")
    assert hasattr(mutagent, "impl")


def test_version():
    assert mutagent.__version__ == "0.1.0"


def test_impl_is_mutobj_impl():
    assert mutagent.impl is mutobj.impl
