"""mutagent.builtins -- Default implementations for mutagent declarations."""

def load():
    from mutagent.runtime.impl_loader import ImplLoader
    ImplLoader.auto_load(__file__, __name__)