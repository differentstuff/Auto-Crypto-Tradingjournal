---
title: "Network Framework"
date: "2026-05-03"
type: reference
tags:
  - reference
  - architecture
  - agent-design
  - synthesis
  - reaction-network
  - pai
status: final
version: "1.0.0"
purpose: "Synthesize Reaction Network Model and PAI Framework into unified architecture"
related:
  - "[[ReactionNetworkModel]]"
  - "[[PAI_Framework]]"
  - "[[WorkingBrief]]"
---

# Network Framework

A unified architecture combining the PAI Framework (WHAT to pursue) with the Reaction Network Model (HOW to execute).

---

## The Core Insight

Two frameworks address the same fundamental challenge from different angles:

| Framework | Question | Provides |
|-----------|----------|----------|
| **PAI Framework** | WHAT to pursue? | Goal definition, success criteria |
| **Reaction Network Model** | HOW to execute? | Execution mechanics, deterministic cascades |

**The synthesis**: Hard-to-vary explanations (PAI) define attractors; enzyme cascades (Reaction Network) reach them.

---

## The Unified Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         NETWORK FRAMEWORK                                │
│                                                                          │
│  ┌─────────────────────────────────────────────────────────────────┐    │
│  │                    DEFINITION LAYER (PAI)                        │    │
│  │                                                                  │    │
│  │   Request ──▶ Reverse ──▶ Ideal State ──▶ Hard-to-Vary          │    │
│  │              Engineer      Criteria       Explanations          │    │
│  │                           (ISC Table)                           │    │
│  │                                 │                               │    │
│  │                                 ▼                               │    │
│  │                          ┌─────────────┐                        │    │
│  │                          │ ATTRACTORS  │ ◀─ Defined goal states │    │
│  │                          └─────────────┘                        │    │
│  └─────────────────────────────────────────────────────────────────┘    │
│                                      │                                   │
│                                      ▼                                   │
│  ┌─────────────────────────────────────────────────────────────────┐    │
│  │                    EXECUTION LAYER (Reaction Network)           │    │
│  │                                                                  │    │
│  │   Substrate ──▶ Enzymes with ──▶ Gradient ──▶ Attractor         │    │
│  │   (State)       Activation      Following     Reached          │    │
│  │                   Conditions                                     │    │
│  │                                 │                               │    │
│  │                                 ▼                               │    │
│  │                          ┌─────────────┐                        │    │
│  │                          │ ISC VERIFIED │ ◀─ All criteria met    │    │
│  │                          └─────────────┘                        │    │
│  └─────────────────────────────────────────────────────────────────┘    │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## Concept Mapping

### Attractors = Ideal State Criteria

| PAI Framework | Reaction Network Model |
|--------------|------------------------|
| ISC Table | Attractor Definition |
| Each ISC entry | Attractor condition |
| Binary verification | Condition check |
| Hill climbing | Gradient following |
| "Done" state | Attractor reached |

**The connection**: ISC entries define the conditions that must be true for the attractor to be "reached."

### Hard-to-Vary Explanations = Strict Activation Conditions

| PAI Framework | Reaction Network Model |
|--------------|------------------------|
| Hard-to-vary explanation | Enzyme specificity |
| "Can't tweak without breaking" | "Can't activate without matching" |
| Irreducible criteria | Required substrate keys |
| Negations (what must NOT happen) | Prohibited states |

**The connection**: An enzyme's activation conditions must be as hard-to-vary as the explanation it serves.

### Reverse Engineering = Substrate Initialization

| PAI Framework | Reaction Network Model |
|--------------|------------------------|
| Reverse engineering | Substrate initialization |
| Explicit wants | Substrate.task |
| Context/assumptions | Substrate.context |
| Pitfalls | Substrate.constraints |
| Implied wants | Substrate.intermediate (derived) |

**The connection**: The reverse engineering phase populates the initial substrate state.

### Euphoric Surprise = Meta-Attractor

| PAI Framework | Reaction Network Model |
|--------------|------------------------|
| Euphoric surprise | Meta-attractor (creative tasks) |
| Subjective recognition | System-level convergence |
| Antecedents articulated | Gradient proxies |

**The connection**: For creative tasks, the attractor isn't a checklist—it's a system state that produces recognition in the observer.

---

## The Unified Workflow

### Phase 1: Definition (PAI Framework)

```yaml
# Step 1: Reverse engineer the request
reverse_engineering:
  explicit_wants:
    - "Research NVIDIA earnings"
    - "Cite sources"
  explicit_not_wants:
    - "No speculation"
    - "No outdated data"
  implied_wants:
    - "Actionable insights"
    - "Clear presentation"
  pitfalls:
    - "Conflicting sources"
    - "Outdated financial data"
  assumptions:
    - "User wants recent data"
    - "User can act on findings"

# Step 2: Generate ISC Table
isc_table:
  - id: "ISC-001"
    criterion: "Sources cited and verifiable"
    verification: "all(source.verified for source in sources)"
    status: "pending"
    
  - id: "ISC-002"
    criterion: "Data is within 30 days"
    verification: "max_age(sources) <= 30 days"
    status: "pending"
    
  - id: "ISC-003"
    criterion: "Key findings summarized"
    verification: "summary.length > 0 AND summary.key_points >= 3"
    status: "pending"
    
  - id: "ISC-004"
    criterion: "Actionable recommendations provided"
    verification: "recommendations.count >= 1"
    status: "pending"
```

### Phase 2: Execution (Reaction Network Model)

```yaml
# Step 3: Initialize Substrate
substrate:
  task: "Research NVIDIA earnings"
  context:
    - "User wants recent data"
    - "User can act on findings"
  intermediate: {}
  constraints:
    - "No speculation"
    - "No outdated data"
    - "Max 30 seconds"
  validity:
    - "Sources must be verifiable"
    - "Data within 30 days"
  pending:
    - "ISC-001"
    - "ISC-002"
    - "ISC-003"
    - "ISC-004"

# Step 4: Define Enzymes with Activation Conditions
enzymes:
  - name: "SearchWeb"
    class: "Sensor"
    activation:
      requires:
        - "substrate.task exists"
        - "NOT substrate.intermediate.search_results"
      prohibits:
        - "substrate.status == 'complete'"
    output: "substrate.intermediate.search_results"
    
  - name: "VerifySources"
    class: "Oxidoreductase"
    activation:
      requires:
        - "substrate.intermediate.search_results exists"
      prohibits:
        - "substrate.validity.all_verified"
    output: "substrate.intermediate.verified_sources"
    
  - name: "ExtractKeyFindings"
    class: "Synthase"
    activation:
      requires:
        - "substrate.intermediate.verified_sources exists"
      prohibits:
        - "substrate.intermediate.summary exists"
    output: "substrate.intermediate.summary"
    
  - name: "GenerateRecommendations"
    class: "Synthase"
    activation:
      requires:
        - "substrate.intermediate.summary exists"
      prohibits:
        - "substrate.intermediate.recommendations exists"
    output: "substrate.intermediate.recommendations"

# Step 5: Define Attractors
attractors:
  - name: "research_complete"
    conditions:
      - "ISC-001 verified"
      - "ISC-002 verified"
      - "ISC-003 verified"
      - "ISC-004 verified"
      - "substrate.pending is empty"
```

### Phase 3: Execution Loop

```python
def execute_network(substrate, enzymes, isc_table, attractors):
    """
    Unified execution combining PAI definition with Reaction Network execution.
    """
    while not at_attractor(substrate, attractors):
        # Find activatable enzymes
        activatable = [e for e in enzymes if e.can_activate(substrate)]
        
        if not activatable:
            raise DeadlockError("No enzyme can activate - ISC may be unreachable")
        
        # Calculate flux scores (progress toward attractors)
        scores = [flux_score(e, substrate, attractors) for e in activatable]
        
        # Fire the enzyme with highest flux
        best = activatable[argmax(scores)]
        substrate = best.transform(substrate)
        
        # Verify ISC entries
        for isc in isc_table:
            if isc.can_verify(substrate):
                isc.status = "verified" if isc.verify(substrate) else "failed"
        
        # Update pending
        substrate.pending = [isc.id for isc in isc_table if isc.status == "pending"]
    
    return substrate
```

---

## Practical Integration

### For the Reaction Network Model

**Add ISC Generation Phase:**

```python
class ReactionNetwork:
    def __init__(self, request):
        # PAI Phase: Define WHAT
        self.reverse_engineered = reverse_engineer(request)
        self.isc_table = generate_isc(self.reverse_engineered)
        self.attractors = isc_to_attractors(self.isc_table)
        
        # Reaction Network Phase: Define HOW
        self.substrate = initialize_substrate(self.reverse_engineered)
        self.enzymes = define_enzymes(self.isc_table)
```

**Add Hard-to-Vary Validation:**

```python
def validate_enzyme(enzyme, isc_table):
    """
    Ensure enzyme activation conditions are as hard-to-vary
    as the ISC entries they serve.
    """
    for isc in isc_table:
        if enzyme.serves(isc):
            # Can you change the activation without breaking the ISC?
            # If yes, the activation is too soft.
            assert is_hard_to_vary(enzyme.activation, isc.criterion)
```

**Add Euphoric Surprise Meta-Attractor:**

```python
class CreativeTaskNetwork(ReactionNetwork):
    def __init__(self, request):
        super().__init__(request)
        # For creative tasks, add meta-attractor
        self.meta_attractor = "euphoric_surprise"
        self.antecedents = articulate_antecedents(request)
    
    def verify_euphoric_surprise(self, result, user_feedback):
        """
        Learn from subjective feedback to improve antecedent articulation.
        """
        if user_feedback == "wow":
            self.learn_antecedents(result, self.antecedents)
```

### For the PAI Framework

**Add Enzyme Classification:**

```yaml
# Classify ISC entries by enzyme type
isc_table:
  - id: "ISC-001"
    criterion: "Sources cited"
    enzyme_class: "Oxidoreductase"  # Evaluates/verifies
    dependencies: []  # Can run immediately
    
  - id: "ISC-002"
    criterion: "Key findings summarized"
    enzyme_class: "Synthase"  # Builds new structure
    dependencies: ["ISC-001"]  # Requires verified sources
    
  - id: "ISC-003"
    criterion: "Recommendations provided"
    enzyme_class: "Synthase"
    dependencies: ["ISC-002"]  # Requires summary
```

**Add Cascading Verification:**

```python
class ISCTable:
    def get_execution_order(self):
        """
        Return ISC entries in dependency order.
        This creates a cascade where outputs feed into inputs.
        """
        order = []
        remaining = list(self.entries)
        
        while remaining:
            # Find ISC entries whose dependencies are satisfied
            ready = [isc for isc in remaining 
                     if all(dep in order for dep in isc.dependencies)]
            
            if not ready:
                raise CircularDependencyError()
            
            order.extend(ready)
            remaining = [isc for isc in remaining if isc not in ready]
        
        return order
```

**Add Gradient Metrics:**

```python
class ISCTable:
    def calculate_gradient(self):
        """
        Replace binary done/not-done with measurable progress.
        Returns a score from 0.0 to 1.0.
        """
        verified = sum(1 for isc in self.entries if isc.status == "verified")
        total = len(self.entries)
        
        # Weight by dependency depth (deeper = more progress)
        weighted = sum(isc.depth for isc in self.entries if isc.status == "verified")
        max_weight = sum(isc.depth for isc in self.entries)
        
        return {
            "binary": verified / total,
            "weighted": weighted / max_weight if max_weight > 0 else 0
        }
```

---

## The Unified Checklist

### Definition Layer (PAI Framework)

- [ ] **Request reverse-engineered?** Explicit wants, not-wants, implied, pitfalls?
- [ ] **ISC table created?** Discrete, independent, binary criteria?
- [ ] **Hard-to-vary test passed?** Can you remove any criterion without breaking the goal?
- [ ] **Negations included?** What must NOT happen?
- [ ] **Dependencies mapped?** Which ISC entries enable others?
- [ ] **For creative tasks: Antecedents articulated?** What produces the subjective feeling?

### Execution Layer (Reaction Network Model)

- [ ] **Substrate defined?** Persistent state container with all required fields?
- [ ] **Enzymes classified?** Each tool has a single class (Sensor, Synthase, etc.)?
- [ ] **Activation conditions specified?** Enzymes only fire when appropriate?
- [ ] **Deterministic outputs?** Same substrate produces same result?
- [ ] **Attractors defined?** Goal states explicit in terms of ISC?
- [ ] **Gradients measurable?** Progress can be quantified?
- [ ] **Cascades composable?** Outputs feed into inputs?
- [ ] **No side effects?** Tools only affect their specified inputs?

### Integration Layer

- [ ] **ISC entries map to attractor conditions?** Definition layer feeds execution layer?
- [ ] **Enzyme activation matches ISC specificity?** Execution respects definition constraints?
- [ ] **Verification happens during execution?** Not just at the end?
- [ ] **Deadlock detection?** System knows when ISC is unreachable?
- [ ] **Learning from creative tasks?** Antecedents improve over time?

---

## Design Patterns

### Pattern 1: ISC-Driven Cascade

```yaml
# ISC entries define the cascade
isc_table:
  - id: "ISC-001"
    criterion: "Data collected"
    enzyme: "Sensor.CollectData"
    output: "substrate.intermediate.raw_data"
    
  - id: "ISC-002"
    criterion: "Data validated"
    enzyme: "Oxidoreductase.ValidateData"
    requires: ["ISC-001"]
    output: "substrate.intermediate.validated_data"
    
  - id: "ISC-003"
    criterion: "Analysis complete"
    enzyme: "Synthase.AnalyzeData"
    requires: ["ISC-002"]
    output: "substrate.intermediate.analysis"
```

### Pattern 2: Attractor Verification

```python
def at_attractor(substrate, attractors):
    """
    Check if substrate has reached any attractor.
    Attractor conditions are ISC entries.
    """
    for attractor in attractors:
        all_conditions_met = all(
            isc_status(isc_id) == "verified"
            for isc_id in attractor.conditions
        )
        if all_conditions_met:
            return attractor
    return None
```

### Pattern 3: Gradient-Guided Selection

```python
def flux_score(enzyme, substrate, attractors):
    """
    Calculate how much this enzyme moves us toward attractors.
    Higher score = more progress toward goal.
    """
    # Simulate the transformation
    simulated = enzyme.transform(copy(substrate))
    
    # Calculate gradient change
    before = calculate_gradient(substrate, attractors)
    after = calculate_gradient(simulated, attractors)
    
    return after - before  # Positive = moving toward attractor
```

### Pattern 4: Learning Antecedents

```python
class EuphoricSurpriseLearner:
    """
    For creative tasks, learn what produces euphoric surprise.
    """
    def __init__(self):
        self.antecedents = []  # Learned patterns
    
    def record_success(self, substrate, result, feedback):
        if feedback == "euphoric_surprise":
            # Extract what conditions led to success
            pattern = extract_pattern(substrate)
            self.antecedents.append(pattern)
    
    def suggest_antecedents(self, request):
        """
        Suggest ISC entries based on learned patterns.
        """
        return [pattern.to_isc() for pattern in self.antecedents 
                if pattern.matches(request)]
```

---

## Anti-Patterns

### Anti-Pattern 1: Mismatched Specificity

```yaml
# Bad: ISC is hard-to-vary, but enzyme activation is soft
isc:
  criterion: "All sources verified with 99% confidence"
enzyme:
  activation:
    requires: ["substrate.sources exists"]  # Too soft!

# Good: Enzyme activation matches ISC specificity
isc:
  criterion: "All sources verified with 99% confidence"
enzyme:
  activation:
    requires:
      - "substrate.sources exists"
      - "all(source.confidence >= 0.99 for source in sources)"
```

### Anti-Pattern 2: Verification Only at End

```python
# Bad: Verify only at the end
def execute(substrate, enzymes):
    for enzyme in enzymes:
        substrate = enzyme.transform(substrate)
    return verify_all(substrate)  # Too late!

# Good: Verify during execution
def execute(substrate, enzymes, isc_table):
    for enzyme in enzymes:
        substrate = enzyme.transform(substrate)
        verify_isc(substrate, isc_table)  # Continuous verification
    return substrate
```

### Anti-Pattern 3: Ignoring Creative Meta-Attractor

```yaml
# Bad: Treat creative task as verifiable checklist
creative_task:
  isc:
    - "Design looks good"
    - "User likes it"

# Good: Articulate antecedents of euphoric surprise
creative_task:
  meta_attractor: "euphoric_surprise"
  antecedents:
    - "Visual hierarchy guides eye to primary CTA within 2 seconds"
    - "Color contrast creates emotional resonance"
    - "Typography matches brand personality"
    - "Layout rhythm creates sense of elegance"
```

---

## The Complete Picture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                           REQUEST                                        │
│                              │                                           │
│                              ▼                                           │
│  ┌─────────────────────────────────────────────────────────────────┐    │
│  │                    REVERSE ENGINEERING                           │    │
│  │  Explicit wants, not-wants, implied, pitfalls, assumptions     │    │
│  └─────────────────────────────────────────────────────────────────┘    │
│                              │                                           │
│                              ▼                                           │
│  ┌─────────────────────────────────────────────────────────────────┐    │
│  │                    ISC TABLE GENERATION                          │    │
│  │  Hard-to-vary criteria, binary verification, dependencies       │    │
│  └─────────────────────────────────────────────────────────────────┘    │
│                              │                                           │
│                              ▼                                           │
│  ┌─────────────────────────────────────────────────────────────────┐    │
│  │                    ATTRACTOR DEFINITION                          │    │
│  │  ISC entries → attractor conditions                             │    │
│  └─────────────────────────────────────────────────────────────────┘    │
│                              │                                           │
│                              ▼                                           │
│  ┌─────────────────────────────────────────────────────────────────┐    │
│  │                    SUBSTRATE INITIALIZATION                      │    │
│  │  Task, context, constraints, validity, pending                  │    │
│  └─────────────────────────────────────────────────────────────────┘    │
│                              │                                           │
│                              ▼                                           │
│  ┌─────────────────────────────────────────────────────────────────┐    │
│  │                    ENZYME DEFINITION                             │    │
│  │  Class, activation conditions, output, flux scoring              │    │
│  └─────────────────────────────────────────────────────────────────┘    │
│                              │                                           │
│                              ▼                                           │
│  ┌─────────────────────────────────────────────────────────────────┐    │
│  │                    EXECUTION LOOP                                │    │
│  │  ┌─────────────────────────────────────────────────────────┐    │    │
│  │  │  Find activatable enzymes                                │    │    │
│  │  │  Calculate flux scores                                    │    │    │
│  │  │  Fire highest-flux enzyme                                 │    │    │
│  │  │  Update substrate                                         │    │    │
│  │  │  Verify ISC entries                                       │    │    │
│  │  │  Check for attractor                                      │    │    │
│  │  └─────────────────────────────────────────────────────────┘    │    │
│  └─────────────────────────────────────────────────────────────────┘    │
│                              │                                           │
│                              ▼                                           │
│  ┌─────────────────────────────────────────────────────────────────┐    │
│  │                    ATTRACTOR REACHED                             │    │
│  │  All ISC verified, pending empty, gradient maximized            │    │
│  └─────────────────────────────────────────────────────────────────┘    │
│                              │                                           │
│                              ▼                                           │
│                           RESULT                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## Summary

The Network Framework unifies two complementary frameworks:

| Layer | Framework | Purpose |
|-------|-----------|---------|
| **Definition** | PAI Framework | Define WHAT to pursue (ISC, hard-to-vary explanations) |
| **Execution** | Reaction Network Model | Define HOW to execute (enzymes, cascades, attractors) |
| **Integration** | Network Framework | Connect definition to execution |

**Key insights:**

1. **ISC entries are attractor conditions** — Definition layer specifies goal states
2. **Enzyme specificity matches ISC hardness** — Execution layer respects definition constraints
3. **Reverse engineering populates substrate** — Definition layer initializes execution layer
4. **Verification happens during execution** — Not just at the end
5. **Euphoric surprise is a meta-attractor** — For creative tasks, antecedents are learned

**The result:** A system that knows WHAT to pursue (PAI) and HOW to get there (Reaction Network), with both layers enforcing hard-to-vary explanations through deterministic execution.

---

## See Also

- [[ReactionNetworkModel]] — HOW to execute (enzyme cascades, attractors, gradients)
- [[PAI_Framework]] — WHAT to pursue (ISC, hard-to-vary explanations, hill climbing)
- [[WorkingBrief]] — Implementation of substrate model