""" llm package -- Provider-agnostic LLM routing with key rotation and budget tracking.

Public API (what enzymes import):
  from llm import call_llm, build_prompt

  call_llm(role, prompt, system=None) -> str | None
    Call the LLM configured for the given role. Never raises.
    Returns str on success, None on any failure.

  build_prompt(substrate, max_chars=4000, priority_order=None) -> str
    Assemble context block for an LLM prompt. Respects hard budget cap.

  load_prompt_file(role, prompts_config) -> str
    Load external prompt file for a role. Returns "" if not found.

  format_enforcement_instruction(response_format) -> str
    Generate soft JSON enforcement instruction for prompts.

  validate_json(response, role) -> (bool, parsed_data)
    Validate and extract JSON from LLM responses.

  extract_json(text) -> str | None
    Extract JSON from text with markdown fences or prose.

  build_retry_prompt(prompt, system, response_format) -> (prompt, system)
    Build stronger prompt for retrying failed JSON responses.

Internal modules (not for direct import by enzymes):
  llm.router          -- LLMRouter class, init_router(), call_llm()
  llm.key_manager     -- KeyManager for API key rotation
  llm.anthropic_client  -- Anthropic SDK client
  llm.gemini_client     -- Google Gemini client (urllib)
  llm.openrouter_client -- OpenRouter client (OpenAI-compatible)
  llm.prompt_builder    -- Context assembler with priority order
  llm.response_parser   -- JSON validation, extraction, retry logic
"""

from llm.router import call_llm, init_router
from llm.prompt_builder import build_prompt, load_prompt_file, format_enforcement_instruction
from llm.response_parser import validate_json, extract_json, build_retry_prompt

# Explicit submodule imports so unittest.mock.patch("llm.anthropic_client.send")
# can resolve the attribute. These do NOT trigger SDK imports — the SDKs are
# lazily imported inside each client's send() function.
from llm import anthropic_client, gemini_client, openrouter_client, response_parser

__all__ = [
    "call_llm",
    "init_router",
    "build_prompt",
    "load_prompt_file",
    "format_enforcement_instruction",
    "validate_json",
    "extract_json",
    "build_retry_prompt",
]