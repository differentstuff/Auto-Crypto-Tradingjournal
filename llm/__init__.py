""" llm package -- Provider-agnostic LLM routing with key rotation and budget tracking.

Public API (what enzymes import):
  from llm import call_llm, build_prompt

  call_llm(role, prompt, system=None) -> str | None
    Call the LLM configured for the given role. Never raises.
    Returns str on success, None on any failure.

  build_prompt(substrate, max_chars=4000, priority_order=None) -> str
    Assemble context block for an LLM prompt. Respects hard budget cap.

Internal modules (not for direct import by enzymes):
  llm.router        -- LLMRouter class, init_router(), call_llm()
  llm.key_manager   -- KeyManager for API key rotation
  llm.anthropic_client  -- Anthropic SDK client
  llm.gemini_client     -- Google Gemini client (urllib)
  llm.openrouter_client -- OpenRouter client (OpenAI-compatible)
  llm.prompt_builder    -- Context assembler with priority order
"""

from llm.router import call_llm, init_router
from llm.prompt_builder import build_prompt

# Explicit submodule imports so unittest.mock.patch("llm.anthropic_client.send")
# can resolve the attribute. These do NOT trigger SDK imports — the SDKs are
# lazily imported inside each client's send() function.
from llm import anthropic_client, gemini_client, openrouter_client

__all__ = ["call_llm", "init_router", "build_prompt"]
