"""mutagent.builtins -- Default implementations for mutagent declarations."""


def load():
    from mutagent.builtins import (  # noqa: F401
        agent_impl,
        claude_impl,
        config_impl,
        inspect_module_impl,
        main_impl,
        patch_module_impl,
        run_code_impl,
        save_module_impl,
        selector_impl,
        view_source_impl,
    )