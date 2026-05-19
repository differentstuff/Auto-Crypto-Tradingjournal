---
title: "Reaction Network Model"
date: "2026-05-03"
type: reference
tags:
  - reference
  - architecture
  - agent-design
  - tool-design
  - reaction-network
  - enzyme-model
status: final
version: "2.0.0"
purpose: "Design reliable, deterministic agent and tool systems using reaction network principles"
related:
  - "[[WorkingBrief]]"
  - "[[FRONTMATTER_SCHEMA]]"
---

# Reaction Network Model

A manual for designing agent systems and tool architectures that work.

---

## The Problem

Most agent systems fail because they treat tools as function calls, not as structured operators in a network.

**What goes wrong:**

- Tools are lists, not enzymes
- Selection happens via single-step reasoning
- No persistent state machine combining tools in cascades
- No termination criteria or feedback signals
- LLMs pick tools like roulette: stochastic, not systematic

The result: unreliable dynamic behavior. Why? Because tools are treated as call endpoints, not as functional units in a network.

---

## The Solution: Think Like Chemistry

In biochemistry, reaction networks work because:

1. **Enzymes have specificity** - They only activate under specific conditions
2. **Substrates determine pathways** - The input state determines which reactions occur
3. **Outputs are deterministic** - Same input, same output
4. **Cascades are natural** - The output of one reaction becomes input to another
5. **Regulation exists** - Meta-levels activate or deactivate pathways

This is not metaphor. This is architecture.

---

## Core Concepts

### Substrate

The input material that enzymes act upon.

**In programming:** The data structure, state object, or context that functions operate on.

```yaml
# Example: Working Brief as substrate
substrate:
  task: "Research NVIDIA earnings"
  context:
    - previous_findings
    - constraints
  intermediate: []
  constraints:
    - "max 30 seconds"
    - "cite sources"
  validity:
    - "must have date"
    - "must have source"
  pending: []
```

### Enzyme

A catalyst that transforms substrates into products. Enzymes have:

- **Specificity**: Only bind to matching substrates
- **Activation conditions**: Only activate when conditions are right
- **Deterministic output**: Same substrate, same product

**In programming:** A function or tool that:

- Has strict input requirements (preconditions)
- Produces deterministic output
- Only executes when conditions are met

```yaml
# Example: Tool as enzyme
name: ExtractEarnings
activation:
  requires:
    substrate_keys: ["task"]
    state: ["pending"]
  prohibits:
    substrate_keys: ["intermediate.earnings"]
  generates:
    keys: ["intermediate.earnings"]
transform: "Extract earnings data from source"
output: "Structured earnings data"
```

### Attractor

A stable state the system converges toward. Systems naturally flow toward attractors.

**In programming:** The desired end state of a workflow.

```yaml
# Example: Attractor states
attractors:
  - name: "research_complete"
    conditions:
      - "intermediate.answer exists"
      - "validity.all_passed"
      - "pending is empty"
  
  - name: "brief_ready"
    conditions:
      - "definition_of_done is set"
      - "execution_packet is set"
      - "context_debt is zero"
```

### Gradient

The direction of improvement. Systems follow gradients toward attractors.

**In programming:** A measurable metric showing progress toward the goal.

```yaml
# Example: Gradients
gradients:
  - name: "context_debt"
    direction: "minimize"
    measure: "tokens needed to reconstruct state"
  
  - name: "completeness"
    direction: "maximize"
    measure: "required fields filled"
```

### Activation Conditions

The specific conditions under which an enzyme becomes active. This is the key to deterministic behavior.

**In programming:** Preconditions that must be true before a function executes.

```yaml
# Example: Activation rules
activation:
  requires:
    - "substrate.task exists"
    - "substrate.context is array"
    - "NOT substrate.intermediate.answer"
  prohibits:
    - "substrate.status == 'complete'"
  generates:
    - "substrate.intermediate.{output}"
```

---

## Enzyme Classes

Like metabolic enzymes, tools fall into functional classes:

| Class | Function | Example Tools |
|-------|----------|---------------|
| **Sensor** | Extract data from environment | `web_search`, `read_file`, `fetch_url` |
| **Isomerase** | Transform data (mapping, parsing) | `parse_json`, `normalize_data`, `format_output` |
| **Synthase** | Build new structures | `create_brief`, `generate_report`, `compose_query` |
| **Ligase** | Combine multiple inputs | `merge_data`, `aggregate_results`, `synthesize` |
| **Hydrolase** | Break apart (chunking, tokenizing) | `split_text`, `extract_sections`, `chunk_document` |
| **Oxidoreductase** | Evaluate, score, rank | `rank_results`, `validate_schema`, `score_relevance` |
| **Transporter** | Move data between systems | `save_file`, `send_api`, `store_memory` |

Each class has a single responsibility. No "super-tools" that do everything.

---

## Application to Programming

### Step 1: Define Your Substrate

Create a persistent state container, not just chat history:

```python
state = {
    "task": str,           # What we're doing
    "context": list,       # What we know
    "intermediate": dict,  # What we've computed
    "constraints": list,   # What we must satisfy
    "validity": list,      # What must be true
    "pending": list       # What's left to do
}
```

Each tool modifies only one part of this substrate.

### Step 2: Define Activation Rules

Every tool gets activation conditions:

```python
def can_activate(tool, substrate):
    # Check required keys
    for key in tool.activation.requires:
        if not has_key(substrate, key):
            return False
    
    # Check prohibited states
    for key in tool.activation.prohibits:
        if has_key(substrate, key):
            return False
    
    return True
```

This enforces "chemical precision" - tools only fire when conditions match.

### Step 3: Make the Agent a Regulator

The agent does not randomly pick tools. It:

1. Observes which tools are **activatable**
2. Calculates a **flux score** (how close does this tool bring us to the attractor?)
3. Fires the tool
4. Updates the substrate
5. Evaluates progress
6. Repeats

The agent is a **flux controller**, not an executor.

### Step 4: Define Attractors

States that are "good":

```python
attractors = [
    {
        "name": "task_complete",
        "conditions": [
            "intermediate.answer exists",
            "validity.all(conditions_met)",
            "pending is empty"
        ]
    },
    {
        "name": "brief_ready",
        "conditions": [
            "definition_of_done is set",
            "execution_packet is set",
            "context_debt == 0"
        ]
    }
]
```

These attractors act as metabolic targets the agent seeks.

---

## Application to AI Tool Design

### The Working Brief Example

The Working Brief is a substrate. Each workflow is an enzyme.

| Workflow | Class | Transform |
|----------|-------|-----------|
| Read | Sensor | Reads substrate into working memory |
| Create | Synthase | Raw session state into structured brief |
| Update | Ligase | Existing brief + new decisions into updated brief |
| Compress | Hydrolase | Bloated brief into minimal brief |
| Archive | Transporter | Active brief into long-term storage |

**Activation conditions:**

```yaml
Read:
  requires: []
  generates: ["working_memory"]

Create:
  requires: ["session_state"]
  generates: ["brief"]

Update:
  requires: ["brief", "new_artifacts"]
  generates: ["updated_brief"]

Compress:
  requires: ["brief"]
  prohibits: ["brief.status == 'minimal'"]
  generates: ["compressed_brief"]

Archive:
  requires: ["brief.status == 'complete'"]
  generates: ["archived_brief"]
```

**Attractor:**

```yaml
attractor:
  name: "execution_packet"
  conditions:
    - "brief.definition_of_done is set"
    - "brief.execution_packet is set"
    - "brief.context_debt == 0"
```

The gradient is `context_debt`: tokens needed to reconstruct state from scratch. The system naturally minimizes this.

### Reaction Model

```
Substrate:  session_state (goals, decisions, artifacts, open threads, constraints)
Attractor:  execution_packet (next agent acts immediately, zero re-reading)
Gradient:   context_debt = tokens needed to reconstruct state from scratch
            -> WorkingBrief drives context_debt -> 0
```

---

## Design Patterns

### Pattern 1: Strict Input Contracts

```python
# Bad: Tool accepts anything
def search(query):
    return web_search(query)

# Good: Tool requires specific substrate state
def search_enzyme(substrate):
    if not substrate.get("task"):
        raise ActivationError("Missing required: task")
    if substrate.get("intermediate.answer"):
        raise ActivationError("Prohibited: answer already exists")
    
    result = web_search(substrate.task)
    substrate.intermediate["search_results"] = result
    return substrate
```

### Pattern 2: Composable Cascades

```python
# Tools chain naturally when outputs match inputs
cascade = [
    Sensor("search"),
    Isomerase("parse"),
    Oxidoreductase("rank"),
    Synthase("compose"),
    Transporter("save")
]

# Each tool's output becomes the next tool's input
for enzyme in cascade:
    if enzyme.can_activate(substrate):
        substrate = enzyme.transform(substrate)
```

### Pattern 3: Attractor-Driven Termination

```python
def run_until_attractor(substrate, enzymes, attractors):
    while not at_attractor(substrate, attractors):
        # Find all activatable enzymes
        activatable = [e for e in enzymes if e.can_activate(substrate)]
        
        if not activatable:
            raise DeadlockError("No enzyme can activate")
        
        # Calculate flux scores (progress toward attractor)
        scores = [flux_score(e, substrate, attractors) for e in activatable]
        
        # Fire the best one
        best = activatable[argmax(scores)]
        substrate = best.transform(substrate)
    
    return substrate
```

### Pattern 4: Gradient Following

```python
def flux_score(enzyme, substrate, attractors):
    """Calculate how much this enzyme moves us toward attractors."""
    # Simulate the transformation
    simulated = enzyme.transform(copy(substrate))
    
    # Measure gradient change
    before = gradient_value(substrate, attractors)
    after = gradient_value(simulated, attractors)
    
    return after - before  # Positive = moving toward attractor
```

---

## Anti-Patterns

### Anti-Pattern 1: Super-Tools

```python
# Bad: One tool does everything
def super_tool(query):
    results = search(query)
    parsed = parse(results)
    ranked = rank(parsed)
    return compose(ranked)

# Good: Separate enzymes
class SearchEnzyme(Enzyme):
    activation = {"requires": ["query"]}
    output = "search_results"

class ParseEnzyme(Enzyme):
    activation = {"requires": ["search_results"]}
    output = "parsed_data"
```

### Anti-Pattern 2: Optional Behavior

```python
# Bad: Tool behavior depends on hidden state
def flexible_tool(input):
    if some_global_state:
        return do_thing_a(input)
    else:
        return do_thing_b(input)

# Good: Explicit activation conditions
class ThingAEnzyme(Enzyme):
    activation = {"requires": ["input"], "prohibits": ["global_state"]}
    
class ThingBEnzyme(Enzyme):
    activation = {"requires": ["input", "global_state"]}
```

### Anti-Pattern 3: Stochastic Selection

```python
# Bad: LLM picks tool randomly
tool = llm.pick_from(tools)

# Good: Deterministic activation + gradient scoring
activatable = [t for t in tools if t.can_activate(substrate)]
tool = max(activatable, key=lambda t: flux_score(t, substrate, attractors))
```

---

## Why This Works

The principles translate directly:

| Biology | Programming |
|---------|-------------|
| Modularity (enzymes) | Single-responsibility functions |
| Selectivity (specificity) | Strict input contracts |
| Dynamic flow (metabolism) | State machine transitions |
| Spatial structure (compartments) | Functional spaces |
| Self-organization (attractors) | Convergence to goals |
| Rule-based activation (catalysis) | Preconditions and guards |

---

## Checklist

When designing a tool system:

- [ ] **Substrate defined?** Is there a persistent state container?
- [ ] **Enzyme classes assigned?** Does each tool have a single class?
- [ ] **Activation conditions specified?** Can tools only fire when appropriate?
- [ ] **Deterministic outputs?** Same input, same output?
- [ ] **Attractors defined?** Are goal states explicit?
- [ ] **Gradients measurable?** Can progress be quantified?
- [ ] **Cascades composable?** Do outputs feed into inputs?
- [ ] **No side effects?** Do tools only affect their specified inputs?

---

## Origin

The reaction network model comes from:

1. **Biochemistry** - Enzyme kinetics, metabolic pathways, Michaelis-Menten dynamics
2. **Chemical Reaction Network Theory (CRNT)** - Mathematical analysis of reaction systems, deficiency theory, stability analysis
3. **Systems Biology** - Metabolic control analysis, flux balance analysis
4. **Complexity Science** - Attractor landscapes, self-organization, emergent behavior

Key references:

- Feinberg, M. (2019). *Foundations of Chemical Reaction Network Theory*
- Horn, F. & Jackson, R. (1972). General mass action kinetics. *Archive for Rational Mechanics and Analysis*
- Anderson, D.F. et al. (2014). Stochastic analysis of biochemical reaction networks
- Catalyst.jl - Modern implementation for computational reaction networks

---

## Summary

The reaction network model treats tools as enzymes in a metabolic system:

1. **Substrate** = State container (data, context, intermediate results)
2. **Enzyme** = Tool with strict activation conditions
3. **Attractor** = Desired end state
4. **Gradient** = Measurable progress toward attractor
5. **Activation** = Preconditions that must be met

The agent becomes a **flux regulator** - observing which enzymes can activate, scoring them by progress, and firing the best one.

This produces:

- Reliable tool selection
- Deterministic cascades
- Natural termination
- Reduced hallucination
- Better scaling

---

## See Also

- [[WorkingBrief]] - Implementation of substrate model
- [[FRONTMATTER_SCHEMA]] - Data structure standards
- [[Research/QuickResearch]] - Example of enzyme cascade design