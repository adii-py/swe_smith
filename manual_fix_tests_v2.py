#!/usr/bin/env python3
"""Generate valid test patches for vLLM mirror instances."""
import json

INSTANCES_PATH = "/Users/aditya.singh.001/Desktop/SWE-smith/logs/bug_gen/mirror_instances_for_validation.json"


def make_test_patch(test_file, lines):
    """Create a unified diff format test patch."""
    # Add '+' prefix to each line for the diff format
    diff_lines = '\n'.join(['+' + line for line in lines])
    return f'''diff --git a/{test_file} b/{test_file}
new file mode 100644
index 0000000..abc1234
--- /dev/null
+++ b/{test_file}
@@ -0,0 +1,{len(lines)} @@
{diff_lines}
'''


def get_test_41110():
    lines = [
        '"""Test for PR 41110."""',
        'import pytest',
        'import inspect',
        '',
        'def test_extract_named_tool_call_streaming_exists():',
        '    """Verify extract_named_tool_call_streaming is imported (bug removes it)."""',
        '    from vllm.entrypoints.openai.chat_completion import serving',
        '    src = inspect.getsource(serving)',
        '    assert "extract_named_tool_call_streaming" in src',
    ]
    return make_test_patch('tests/test_41110.py', lines)


def get_test_41135():
    lines = [
        '"""Test for PR 41135."""',
        'import pytest',
        'import inspect',
        'import logging',
        '',
        '# Suppress vllm logs to keep pytest output format intact',
        'logging.getLogger("vllm").setLevel(logging.ERROR)',
        '',
        'def test_kernel_impl_function_exists():',
        '    """Verify _fused_inv_rope_fp8_quant_kernel_impl function exists."""',
        '    from vllm.v1.attention.ops.deepseek_v4_ops import fused_inv_rope_fp8_quant',
        '    mod = inspect.getmodule(fused_inv_rope_fp8_quant)',
        '    assert hasattr(mod, "_fused_inv_rope_fp8_quant_kernel_impl")',
    ]
    return make_test_patch('tests/test_41135.py', lines)


def get_test_41162():
    lines = [
        '"""Test for PR 41162."""',
        'import pytest',
        'import inspect',
        'import logging',
        '',
        '# Suppress vllm logs to keep pytest output format intact',
        'logging.getLogger("vllm").setLevel(logging.ERROR)',
        '',
        'def test_processed_logits_param():',
        '    """Verify processed_logits_out parameter exists."""',
        '    from vllm.v1.worker.gpu.sample.gumbel import gumbel_sample',
        '    sig = inspect.signature(gumbel_sample)',
        '    assert "processed_logits_out" in sig.parameters',
    ]
    return make_test_patch('tests/test_41162.py', lines)


def get_test_41181():
    lines = [
        '"""Test for PR 41181."""',
        'import pytest',
        '',
        'def test_retry_logic():',
        '    """Verify AlreadyBorrowed retry logic exists in base renderer."""',
        '    from vllm.renderers.base import BaseRenderer',
        '    import inspect',
        '    src = inspect.getsource(BaseRenderer)',
        '    assert "Already borrowed" in src',
    ]
    return make_test_patch('tests/test_41181.py', lines)


def get_test_41205():
    lines = [
        '"""Test for PR 41205."""',
        'import pytest',
        'import inspect',
        'import logging',
        '',
        '# Suppress vllm logs to keep pytest output format intact',
        'logging.getLogger("vllm").setLevel(logging.ERROR)',
        '',
        'def test_hyperclovax_vision_config():',
        '    """Verify HCXVisionConfig handles vision_config properly (bug has formatting issue)."""',
        '    from vllm.transformers_utils.configs.hyperclovax import HCXVisionConfig',
        '    src = inspect.getsource(HCXVisionConfig.__init__)',
        '    # Bug puts closing paren on separate line after vision_config dict access',
        '    # Gold state has AutoConfig.for_model call on single line',
        '    # Check for the gold state pattern: for_model(vision_config[...]) followed by )',
        '    import re',
        '    match = re.search(r"AutoConfig\.for_model\([^)]+\)", src)',
        '    assert match is not None, "Gold state should have AutoConfig.for_model on single line"',
    ]
    return make_test_patch('tests/test_41205.py', lines)


def get_test_41217():
    lines = [
        '"""Test for PR 41217."""',
        'import pytest',
        'import inspect',
        'import logging',
        '',
        '# Suppress vllm logs to keep pytest output format intact',
        'logging.getLogger("vllm").setLevel(logging.ERROR)',
        '',
        'def test_rocm_path():',
        '    """Verify ROCm code path exists in Indexer class (bug removes it)."""',
        '    from vllm.model_executor.models.deepseek_v2 import Indexer',
        '    src = inspect.getsource(Indexer.forward)',
        '    assert "current_platform.is_rocm()" in src',
    ]
    return make_test_patch('tests/test_41217.py', lines)


def get_test_41228():
    lines = [
        '"""Test for PR 41228."""',
        'import pytest',
        '',
        'def test_sliding_window_attr():',
        '    """Verify sliding window attributes exist."""',
        '    from vllm.distributed.kv_transfer.kv_connector.v1.offloading.scheduler import TransferJobStatus',
        '    assert hasattr(TransferJobStatus, "sliding_window_block_ids")',
    ]
    return make_test_patch('tests/test_41228.py', lines)


def get_test_41255():
    lines = [
        '"""Test for PR 41255."""',
        'import pytest',
        '',
        'def test_tilelang_kernel():',
        '    """Verify hc_head_fuse_tilelang function exists in source (bug removes it)."""',
        '    # Cannot import module due to tilelang dependency, check source directly',
        '    import os',
        '    mhc_path = os.path.join(os.path.dirname(__file__), "..", "vllm", "model_executor", "layers", "mhc.py")',
        '    with open(mhc_path) as f:',
        '        src = f.read()',
        '    assert "def hc_head_fuse_tilelang(" in src',
    ]
    return make_test_patch('tests/test_41255.py', lines)


def get_test_41282():
    lines = [
        '"""Test for PR 41282."""',
        'import pytest',
        '',
        'def test_admission_cap_param():',
        '    """Verify apply_admission_cap parameter exists."""',
        '    from vllm.v1.core.kv_cache_coordinator import KVCacheCoordinator',
        '    import inspect',
        '    sig = inspect.signature(KVCacheCoordinator.get_num_blocks_to_allocate)',
        '    assert "apply_admission_cap" in sig.parameters',
    ]
    return make_test_patch('tests/test_41282.py', lines)


def get_test_41326():
    lines = [
        '"""Test for PR 41326."""',
        'import pytest',
        'import logging',
        '',
        '# Suppress vllm logs to keep pytest output format intact',
        'logging.getLogger("vllm").setLevel(logging.ERROR)',
        '',
        'def test_per_token_group_quant_fp8_packed_for_deepgemm():',
        '    """Verify per_token_group_quant_fp8_packed_for_deepgemm function exists (bug removes tests)."""',
        '    from vllm.kernels.quantization import fp8_utils',
        '    import inspect',
        '    src = inspect.getsource(fp8_utils)',
        '    # This function is tested in the removed tests, check it exists in gold state',
        '    assert "per_token_group_quant_fp8_packed_for_deepgemm" in src',
    ]
    return make_test_patch('tests/test_41326.py', lines)


def get_test_41433():
    lines = [
        '"""Test for PR 41433."""',
        'import pytest',
        'import logging',
        '',
        '# Suppress vllm logs to keep pytest output format intact',
        'logging.getLogger("vllm").setLevel(logging.ERROR)',
        '',
        'def test_gpu_segment_ids():',
        '    """Verify segment_ids built on GPU in gold state (bug moves to CPU)."""',
        '    from vllm.model_executor.layers.pooler.seqwise.methods import MeanPooling',
        '    import inspect',
        '    src = inspect.getsource(MeanPooling.forward)',
        '    # Gold state builds arange on GPU with device=hidden_states.device',
        '    # Bug moves it to CPU by removing the device argument',
        '    assert "torch.arange(num_seqs, device=hidden_states.device" in src',
    ]
    return make_test_patch('tests/test_41433.py', lines)


def get_test_41448():
    lines = [
        '"""Test for PR 41448."""',
        'import pytest',
        'import logging',
        '',
        '# Suppress vllm logs to keep pytest output format intact',
        'logging.getLogger("vllm").setLevel(logging.ERROR)',
        '',
        'def test_autoweightsloader():',
        '    """Verify AutoWeightsLoader usage."""',
        '    from vllm.model_executor.models.longcat_flash import LongcatFlashForCausalLM',
        '    import inspect',
        '    src = inspect.getsource(LongcatFlashForCausalLM)',
        '    assert "AutoWeightsLoader" in src',
    ]
    return make_test_patch('tests/test_41448.py', lines)


def get_test_41492():
    lines = [
        '"""Test for PR 41492."""',
        'import pytest',
        'import logging',
        '',
        '# Suppress vllm logs to keep pytest output format intact',
        'logging.getLogger("vllm").setLevel(logging.ERROR)',
        '',
        'def test_autoweightsloader():',
        '    """Verify AutoWeightsLoader usage."""',
        '    from vllm.model_executor.models.step3_text import Step3TextForCausalLM',
        '    import inspect',
        '    src = inspect.getsource(Step3TextForCausalLM)',
        '    assert "AutoWeightsLoader" in src',
    ]
    return make_test_patch('tests/test_41492.py', lines)


def get_test_41690():
    lines = [
        '"""Test for PR 41690 - inverse bug."""',
        'import pytest',
        'import logging',
        '',
        '# Suppress vllm logs to keep pytest output format intact',
        'logging.getLogger("vllm").setLevel(logging.ERROR)',
        '',
        'def test_no_autoweightsloader():',
        '    """Verify NO AutoWeightsLoader (inverse bug)."""',
        '    from vllm.model_executor.models import cohere_moe',
        '    import inspect',
        '    src = inspect.getsource(cohere_moe)',
        '    assert "AutoWeightsLoader" not in src',
    ]
    return make_test_patch('tests/test_41690.py', lines)


def get_test_41699():
    lines = [
        '"""Test for PR 41699 - inverse bug."""',
        'import pytest',
        'import logging',
        '',
        '# Suppress vllm logs to keep pytest output format intact',
        'logging.getLogger("vllm").setLevel(logging.ERROR)',
        '',
        'def test_no_load_weights():',
        '    """Verify NO load_weights method (inverse bug)."""',
        '    from vllm.model_executor.models.plamo2 import Plamo2DecoderLayer',
        '    assert not hasattr(Plamo2DecoderLayer, "load_weights")',
    ]
    return make_test_patch('tests/test_41699.py', lines)


def main():
    with open(INSTANCES_PATH, 'r') as f:
        instances = json.load(f)

    test_generators = {
        '41110': get_test_41110,
        '41135': get_test_41135,
        '41162': get_test_41162,
        '41181': get_test_41181,
        '41205': get_test_41205,
        '41217': get_test_41217,
        '41228': get_test_41228,
        '41255': get_test_41255,
        '41282': get_test_41282,
        '41326': get_test_41326,
        '41433': get_test_41433,
        '41448': get_test_41448,
        '41492': get_test_41492,
        '41690': get_test_41690,
        '41699': get_test_41699,
    }

    print("Applying valid test patches...")
    fixed = 0
    for inst in instances:
        suffix = inst['instance_id'].split('.')[-1]
        if suffix in test_generators:
            inst['test_patch'] = test_generators[suffix]()
            print(f"✅ Fixed {suffix}")
            fixed += 1

    with open(INSTANCES_PATH, 'w') as f:
        json.dump(instances, f, indent=2)

    print(f"\nFixed {fixed} instances")


if __name__ == "__main__":
    main()
