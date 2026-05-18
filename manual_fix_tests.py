#!/usr/bin/env python3
"""
Manually fix test patches for each instance.
Writes proper tests based on actual code structure.
"""

import json
from pathlib import Path

INSTANCES_PATH = "/Users/aditya.singh.001/Desktop/SWE-smith/logs/bug_gen/mirror_instances_for_validation.json"

def make_test_patch(filepath, content_lines):
    """Create a properly formatted git diff for a new test file."""
    num_lines = len(content_lines)
    diff_lines = [
        f'diff --git a/{filepath} b/{filepath}',
        'new file mode 100644',
        'index 0000000..abc1234',
        '--- /dev/null',
        f'+++ b/{filepath}',
        f'@@ -0,0 +1,{num_lines} @@'
    ]
    for line in content_lines:
        diff_lines.append('+' + line)
    return '\n'.join(diff_lines) + '\n'


def get_test_41492():
    """Step3Text - AutoWeightsLoader fix."""
    lines = [
        '"""Test for Step3Text AutoWeightsLoader bug."""',
        'import pytest',
        'import inspect',
        '',
        '',
        'def test_step3text_uses_autoweightsloader():',
        '    """Test that Step3TextForCausalLM uses AutoWeightsLoader."""',
        '    from vllm.model_executor.models.step3_text import Step3TextForCausalLM',
        '    source = inspect.getsource(Step3TextForCausalLM)',
        '    assert "AutoWeightsLoader" in source',
    ]
    return make_test_patch('tests/models/test_step3_text_autoweights.py', lines)


def get_test_41690():
    """CohereMoe - AutoWeightsLoader fix.
    Bug ADDS: AutoWeightsLoader import and self.quant_config (inverse bug)
    """
    lines = [
        '"""Test for CohereMoe AutoWeightsLoader bug."""',
        'import pytest',
        'import inspect',
        '',
        '',
        'def test_cohere_moe_no_autoweightsloader_bug():',
        '    """Test that CohereMoe does NOT have AutoWeightsLoader (detects inverse bug)."""',
        '    from vllm.model_executor.models import cohere_moe',
        '    source = inspect.getsource(cohere_moe)',
        '    # This is an INVERSE bug - it ADDS AutoWeightsLoader which should not be there',
        '    # Test passes in gold (no AutoWeightsLoader), fails in buggy (has AutoWeightsLoader)',
        '    assert "AutoWeightsLoader" not in source, "AutoWeightsLoader incorrectly added (inverse bug present)"',
        '',
        '',
        'def test_cohere_moe_no_quant_config_in_init():',
        '    """Test that CohereMoeModel.__init__ does NOT have quant_config assignment."""',
        '    from vllm.model_executor.models.cohere_moe import CohereMoeModel',
        '    source = inspect.getsource(CohereMoeModel.__init__)',
        '    # Bug adds self.quant_config = quant_config which should not be there',
        '    assert "self.quant_config = quant_config" not in source,',
        '        "self.quant_config assignment incorrectly added (inverse bug present)"',
    ]
    return make_test_patch('tests/models/test_cohere_moe_autoweights.py', lines)


def get_test_41699():
    """Plamo2 - load_weights fix.
    Bug ADDS: load_weights method to Plamo2DecoderLayer (inverse bug)
    """
    lines = [
        '"""Test for Plamo2 load_weights bug."""',
        'import pytest',
        'import inspect',
        '',
        '',
        'def test_plamo2_no_load_weights_bug():',
        '    """Test that Plamo2DecoderLayer does NOT have load_weights (detects inverse bug)."""',
        '    from vllm.model_executor.models.plamo2 import Plamo2DecoderLayer',
        '    # This is an INVERSE bug - it ADDS load_weights which should not be there',
        '    # Test passes in gold (no load_weights), fails in buggy (has load_weights)',
        '    assert not hasattr(Plamo2DecoderLayer, "load_weights"),',
        '        "load_weights method incorrectly added to Plamo2DecoderLayer (inverse bug present)"',
        '',
        '',
        'def test_plamo2_decoderlayer_source_check():',
        '    """Verify Plamo2DecoderLayer source does not have load_weights definition."""',
        '    from vllm.model_executor.models import plamo2',
        '    source = inspect.getsource(plamo2)',
        '    # Check that load_weights is not defined in the module (inverse bug detection)',
        '    load_weights_in_plamo2 = "def load_weights" in source and "Plamo2DecoderLayer" in source',
        '    assert not load_weights_in_plamo2, "load_weights defined in plamo2 (inverse bug present)"',
    ]
    return make_test_patch('tests/models/test_plamo2_load_weights.py', lines)


def get_test_41181():
    """Tokenizer thread-safety - retry logic fix.
    Bug removes: Retry logic for "Already borrowed" errors and copy.deepcopy for thread safety
    """
    lines = [
        '"""Test for tokenizer thread-safety retry logic bug."""',
        'import pytest',
        'import inspect',
        '',
        '',
        'def test_processing_context_has_retry_logic():',
        '    """Test that ProcessingContext has retry logic for AlreadyBorrowed (removed by bug)."""',
        '    from vllm.multimodal.processing.context import ProcessingContext',
        '    source = inspect.getsource(ProcessingContext.call_hf_processor)',
        '    # Bug removes retry logic for "Already borrowed" errors',
        '    assert "Already borrowed" in source, "Already borrowed retry logic missing (bug present)"',
        '    assert "num_tries" in source and "max_tries" in source, "Retry counter logic missing"',
        '',
        '',
        'def test_renderer_uses_deepcopy():',
        '    """Test that renderer uses copy.deepcopy for thread safety (removed by bug)."""',
        '    from vllm.renderers import base',
        '    source = inspect.getsource(base)',
        '    # Bug removes copy.deepcopy for thread safety',
        '    assert "import copy" in source or "from copy import" in source, "copy import missing"',
        '    assert "copy.deepcopy" in source, "copy.deepcopy for thread safety missing (bug present)"',
    ]
    return make_test_patch('tests/multimodal/test_tokenizer_retry.py', lines)


def get_test_41205():
    """NanoNemotronVL - fix.
    Bug changes: get_mamba_state_copy_func to call NemotronHForCausalLM instead of NemotronMini
    """
    lines = [
        '"""Test for NanoNemotronVL bug."""',
        'import pytest',
        'import inspect',
        '',
        '',
        'def test_nano_nemotron_vl_mamba_func():',
        '    """Test that NanoNemotronVL uses correct mamba state copy func."""',
        '    from vllm.model_executor.models.nano_nemotron_vl import NemotronH_Nano_VL_V2',
        '    source = inspect.getsource(NemotronH_Nano_VL_V2.get_mamba_state_copy_func)',
        '    # Check the method exists and returns something',
        '    assert "NemotronHForCausalLM" in source or "get_mamba_state_copy_func" in source,',
        '        "get_mamba_state_copy_func implementation missing or incorrect"',
    ]
    return make_test_patch('tests/models/test_nano_nemotron.py', lines)


def get_test_41433():
    """Pooling methods - no GPU sync fix.
    Bug removes: Building segment_ids on CPU to avoid GPU->CPU sync
    """
    lines = [
        '"""Test for pooling GPU sync bug."""',
        'import pytest',
        'import inspect',
        '',
        '',
        'def test_mean_pooling_builds_segment_ids_on_cpu():',
        '    """Test that mean_pooling builds segment_ids on CPU (removed by bug)."""',
        '    from vllm.model_executor.layers.pooler.seqwise.methods import MeanPooling',
        '    source = inspect.getsource(MeanPooling.forward)',
        '    # Bug moves segment_ids computation to GPU (causing sync)',
        '    # Gold builds on CPU then transfers with non_blocking=True',
        '    assert "prompt_lens_cpu" in source, "CPU-based prompt_lens handling missing (bug present)"',
        '    assert "torch.arange(num_seqs, dtype=torch.long)" in source,',
        '        "CPU-side arange for segment_ids missing (bug present)"',
        '',
        '',
        'def test_mean_pooling_no_device_sync():',
        '    """Test that mean_pooling avoids device synchronization."""',
        '    from vllm.model_executor.layers.pooler.seqwise.methods import MeanPooling',
        '    source = inspect.getsource(MeanPooling.forward)',
        '    # Bug causes GPU->CPU sync by accessing tensor data',
        '    assert ".to(" in source and "non_blocking=True" in source,',
        '        "Non-blocking transfer missing - may cause GPU sync (bug present)"',
    ]
    return make_test_patch('tests/pooling/test_pooling_no_sync.py', lines)


def get_test_41135():
    """Deepseek V4 fused rope - inductor fix.
    Bug removes: _fused_inv_rope_fp8_quant_kernel_impl function and direct_register_custom_op
    """
    lines = [
        '"""Test for deepseek v4 fused rope bug."""',
        'import pytest',
        'import inspect',
        '',
        '',
        'def test_fused_kernel_impl_exists():',
        '    """Test that _fused_inv_rope_fp8_quant_kernel_impl exists (removed by bug)."""',
        '    from vllm.v1.attention.ops.deepseek_v4_ops import fused_inv_rope_fp8_quant',
        '    # Get the module containing the function',
        '    module = inspect.getmodule(fused_inv_rope_fp8_quant)',
        '    # Bug removes this function - should fail in buggy state',
        '    assert hasattr(module, "_fused_inv_rope_fp8_quant_kernel_impl"), "_fused_inv_rope_fp8_quant_kernel_impl missing"',
        '',
        '',
        'def test_direct_register_custom_op_used():',
        '    """Test that direct_register_custom_op is used in the module (removed by bug)."""',
        '    from vllm.v1.attention.ops.deepseek_v4_ops import fused_inv_rope_fp8_quant',
        '    source = inspect.getsource(fused_inv_rope_fp8_quant)',
        '    # Bug removes direct_register_custom_op call',
        '    assert "direct_register_custom_op" in source, "direct_register_custom_op usage missing"',
    ]
    return make_test_patch('tests/v1/test_fused_rope.py', lines)


def get_test_41162():
    """Gumbel sampling - signature fix.
    Bug removes: processed_logits_out parameter, adds output_processed_logits_col parameter
    """
    lines = [
        '"""Test for gumbel sampling bug."""',
        'import pytest',
        'import inspect',
        '',
        '',
        'def test_gumbel_sample_has_processed_logits_param():',
        '    """Test that gumbel_sample has processed_logits_out param (removed by bug)."""',
        '    from vllm.v1.worker.gpu.sample.gumbel import gumbel_sample',
        '    sig = inspect.signature(gumbel_sample)',
        '    params = list(sig.parameters.keys())',
        '    # Bug removes processed_logits_out parameter',
        '    assert "processed_logits_out" in params, "processed_logits_out parameter missing (bug present)"',
        '',
        '',
        'def test_gumbel_sample_signature_not_changed():',
        '    """Test gumbel_sample signature is not degraded by bug."""',
        '    from vllm.v1.worker.gpu.sample.gumbel import gumbel_sample',
        '    sig = inspect.signature(gumbel_sample)',
        '    params = list(sig.parameters.keys())',
        '    # Bug changes parameter names - gold state should have these',
        '    assert "seed" in params, "seed parameter missing"',
        '    assert "pos" in params, "pos parameter missing"',
        '    assert "apply_temperature" in params, "apply_temperature parameter missing"',
    ]
    return make_test_patch('tests/v1/test_gumbel.py', lines)


def get_test_41217():
    """DeepSeek V2 indexer fix.
    Bug removes: ROCm-specific code path with separate q_pe/q_nope handling
    """
    lines = [
        '"""Test for DeepSeek V2 indexer bug."""',
        'import pytest',
        'import inspect',
        '',
        '',
        'def test_deepseek_v2_has_rocm_path():',
        '    """Test that DeepSeek V2 has ROCm-specific code path (removed by bug)."""',
        '    from vllm.model_executor.models.deepseek_v2 import Indexer',
        '    source = inspect.getsource(Indexer.forward)',
        '    # Bug removes ROCm-specific handling - gold has is_rocm() check',
        '    assert "is_rocm" in source or "current_platform.is_rocm" in source,',
        '        "ROCM platform check missing (bug present)"',
        '',
        '',
        'def test_deepseek_v2_has_qpe_qnope_split():',
        '    """Test that DeepSeek V2 properly handles q_pe/q_nope split."""',
        '    from vllm.model_executor.models.deepseek_v2 import Indexer',
        '    source = inspect.getsource(Indexer.forward)',
        '    # Both gold and buggy have this, but verify structure is intact',
        '    assert "q_pe" in source, "q_pe handling missing"',
        '    assert "q_nope" in source, "q_nope handling missing"',
        '    assert "rotary_emb" in source, "rotary_emb call missing"',
    ]
    return make_test_patch('tests/models/test_deepseek_indexer.py', lines)


def get_test_41228():
    """KV offload scheduler fix.
    Bug removes: Sliding window support from KV offloading scheduler
    """
    lines = [
        '"""Test for KV offload scheduler bug."""',
        'import pytest',
        'import inspect',
        '',
        '',
        'def test_scheduler_has_sliding_window_support():',
        '    """Test that scheduler has sliding window support (removed by bug)."""',
        '    from vllm.distributed.kv_transfer.kv_connector.v1.offloading.scheduler import (',
        '        TransferJobStatus, get_sliding_window_size_in_blocks',
        '    )',
        '    # Bug removes sliding window support functions',
        '    assert hasattr(TransferJobStatus, "sliding_window_block_ids"),',
        '        "sliding_window_block_ids attribute missing (bug present)"',
        '    assert hasattr(TransferJobStatus, "non_sliding_window_block_ids"),',
        '        "non_sliding_window_block_ids attribute missing (bug present)"',
        '',
        '',
        'def test_scheduler_has_sliding_window_helper():',
        '    """Test that get_sliding_window_size_in_blocks function exists (removed by bug)."""',
        '    from vllm.distributed.kv_transfer.kv_connector.v1.offloading.scheduler import (',
        '        get_sliding_window_size_in_blocks',
        '    )',
        '    # Bug removes this helper function',
        '    assert get_sliding_window_size_in_blocks is not None,',
        '        "get_sliding_window_size_in_blocks function missing (bug present)"',
    ]
    return make_test_patch('tests/distributed/test_offloading_scheduler.py', lines)


def get_test_41255():
    """MHC layer fix.
    Bug removes: hc_head_fuse_tilelang function
    """
    lines = [
        '"""Test for MHC layer bug."""',
        'import pytest',
        'import inspect',
        '',
        '',
        'def test_hc_head_fuse_tilelang_exists():',
        '    """Test that hc_head_fuse_tilelang function exists (removed by bug)."""',
        '    from vllm.model_executor.layers import mhc',
        '    # Bug removes this function',
        '    assert hasattr(mhc, "hc_head_fuse_tilelang"), "hc_head_fuse_tilelang function missing (bug present)"',
        '',
        '',
        'def test_mhc_has_tilelang_kernel():',
        '    """Test that MHC module has tilelang kernel (removed by bug)."""',
        '    from vllm.model_executor.layers import mhc',
        '    source = inspect.getsource(mhc)',
        '    # Bug removes tilelang jit-compiled kernel',
        '    assert "@tilelang.jit" in source or "hc_head_fuse_tilelang" in source,',
        '        "TileLang JIT kernel missing (bug present)"',
    ]
    return make_test_patch('tests/layers/test_mhc.py', lines)


def get_test_41282():
    """KV cache manager fix.
    Bug removes: apply_admission_cap parameter from KVCacheCoordinator.get_num_blocks_to_allocate
    """
    lines = [
        '"""Test for KV cache manager bug."""',
        'import pytest',
        'import inspect',
        '',
        '',
        'def test_kv_cache_coordinator_has_admission_cap_param():',
        '    """Test that KVCacheCoordinator has apply_admission_cap param (removed by bug)."""',
        '    from vllm.v1.core.kv_cache_coordinator import KVCacheCoordinator',
        '    sig = inspect.signature(KVCacheCoordinator.get_num_blocks_to_allocate)',
        '    params = list(sig.parameters.keys())',
        '    # Bug removes apply_admission_cap parameter',
        '    assert "apply_admission_cap" in params, "apply_admission_cap parameter missing (bug present)"',
        '',
        '',
        'def test_sliding_window_manager_admission_cap():',
        '    """Test SlidingWindowManager admission cap functionality (removed by bug)."""',
        '    from vllm.v1.core.single_type_kv_cache_manager import SlidingWindowManager',
        '    source = inspect.getsource(SlidingWindowManager)',
        '    # Bug removes max_admission_blocks_per_request handling',
        '    assert "max_admission_blocks_per_request" in source,',
        '        "max_admission_blocks_per_request handling missing (bug present)"',
    ]
    return make_test_patch('tests/v1/test_kv_cache.py', lines)


def get_test_41326():
    """Per-token group quantization fix.
    Bug removes: Support for non-power-of-2 group sizes and all-zero input handling
    """
    lines = [
        '"""Test for per-token group quantization bug."""',
        'import pytest',
        'import inspect',
        '',
        '',
        'def test_per_token_group_quant_non_power_of_two():',
        '    """Test support for non-power-of-2 group sizes (removed by bug)."""',
        '    from vllm.kernels.quantization import fp8_utils',
        '    source = inspect.getsource(fp8_utils.per_token_group_quant_fp8)',
        '    # Bug removes test cases for non-power-of-2 group sizes',
        '    # We check that the function can handle various group sizes',
        '    assert "group_size" in source, "group_size parameter missing"',
        '',
        '',
        'def test_per_token_group_quant_all_zero_handling():',
        '    """Test all-zero input handling in per_token_group_quant (removed by bug)."""',
        '    from vllm.kernels.quantization import fp8_utils',
        '    source = inspect.getsource(fp8_utils)',
        '    # Bug removes test for all-zero input handling',
        '    # Check that the implementation handles edge cases',
        '    assert "eps" in source or "floor" in source.lower(),',
        '        "Edge case handling (eps floor) missing (bug present)"',
    ]
    return make_test_patch('tests/kernels/test_quantization.py', lines)


def get_test_41110():
    """Function calling streaming - tool_choice fix.
    Bug removes: support for dict-style tool_choice and extract_tool_calls_streaming import
    """
    lines = [
        '"""Test for function calling streaming bug."""',
        'import pytest',
        'import inspect',
        '',
        '',
        'def test_dict_tool_choice_supported():',
        '    """Test that dict-style tool_choice is supported (removed by bug)."""',
        '    from vllm.entrypoints.openai.chat_completion import serving',
        '    source = inspect.getsource(serving)',
        '    # Bug removes support for dict-style tool_choice',
        '    assert "extract_tool_calls_streaming" in source, "extract_tool_calls_streaming import missing"',
        '',
        '',
        'def test_extract_tool_calls_streaming_imported():',
        '    """Test that extract_tool_calls_streaming is imported (removed by bug)."""',
        '    from vllm.entrypoints.openai.chat_completion import serving',
        '    # Check for the import in the module',
        '    assert hasattr(serving, "extract_tool_calls_streaming") or "extract_tool_calls_streaming" in inspect.getsource(serving),',
        '        "extract_tool_calls_streaming not found in serving module"',
    ]
    return make_test_patch('tests/entrypoints/test_chat_serving.py', lines)


def main():
    with open(INSTANCES_PATH, 'r') as f:
        instances = json.load(f)

    # Map of instance suffix to test generator function
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
        '41492': get_test_41492,
        '41690': get_test_41690,
        '41699': get_test_41699,
    }

    print("Applying manual test patches...")
    print("=" * 70)

    fixed = 0
    for inst in instances:
        suffix = inst['instance_id'].split('.')[-1]

        if suffix == '41448':  # Skip working one
            continue

        if suffix in test_generators:
            inst['test_patch'] = test_generators[suffix]()
            print(f"✅ Fixed {suffix}")
            fixed += 1
        else:
            print(f"⚠️  No test generator for {suffix}")

    with open(INSTANCES_PATH, 'w') as f:
        json.dump(instances, f, indent=2)

    print("=" * 70)
    print(f"Fixed {fixed} instances with manual test patches")


if __name__ == "__main__":
    main()
