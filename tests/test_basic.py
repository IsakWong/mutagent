"""Basic tests - verify package can be imported."""

import mutagent
import forwardpy


def test_import_mutagent():
    assert hasattr(mutagent, "Object")
    assert hasattr(mutagent, "impl")
    assert hasattr(mutagent, "MutagentMeta")


def test_version():
    assert mutagent.__version__ == "0.1.0"


def test_impl_is_forwardpy_impl():
    assert mutagent.impl is forwardpy.impl
