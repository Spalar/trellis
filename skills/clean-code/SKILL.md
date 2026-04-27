---
name: clean-code
description: Pragmatic coding standards - concise, direct, no over-engineering, no unnecessary comments, Add docstrings make it easy to understand without reading. Always update the doctring when code changes. Follow OOP Principles.
metadata:
  version: 2.3
  priority: CRITICAL
---

# Clean Code - Pragmatic AI Coding Standards

> **CRITICAL SKILL** - Be **concise, direct, and solution-focused**.

---

## Core Principles

| Principle | Rule |
|-----------|------|
| **SRP** | Single Responsibility - each function/class does ONE thing |
| **DRY** | Don't Repeat Yourself - extract duplicates, reuse |
| **KISS** | Keep It Simple - simplest solution that works |
| **YAGNI** | You Aren't Gonna Need It - don't build unused features |
| **Boy Scout** | Leave code cleaner than you found it |

---

## Naming Rules

| Element | Convention |
|---------|------------|
| **Variables** | Reveal intent: `userCount` not `n` |
| **Functions** | Verb + noun: `getUserById()` not `user()` |
| **Booleans** | Question form: `isActive`, `hasPermission`, `canEdit` |
| **Constants** | SCREAMING_SNAKE: `MAX_RETRY_COUNT` |

> **Rule:** If you need a comment to explain a name, rename it.

---

## Docstrings - Just Enough

Add docstrings to functions and classes so AI tools (like Trellis) can extract intent without reading the full implementation.

| What | How |
|------|-----|
| **Functions** | One line: what it does, inputs, outputs |
| **Classes** | One line: responsibility + key public methods |
| **Update** | Change docstring when function signature or purpose changes |

**Example:**
```python
def calculate_discount(order: Order, coupon: str) -> float:
    """Apply coupon discount to order total. Returns discounted amount."""
```

> **Rule:** Docstring = one sentence. Not a novel. Update when code changes.

---

## OOP Principles

| Principle | Practice |
|-----------|----------|
| **Encapsulation** | Private state (`_name`), expose via methods (`get_name()`) |
| **Composition** | Prefer `has-a` over `is-a` — compose objects instead of deep inheritance |
| **Interfaces** | Define contracts (abstract classes / protocols), depend on abstractions |
| **Polymorphism** | Same interface, different implementations — let caller decide |

**Example:**
```python
class PaymentProcessor(ABC):
    def process(self, amount: float) -> bool: ...

class StripeProcessor(PaymentProcessor): ...
class PaypalProcessor(PaymentProcessor): ...
```

> **Rule:** Deep inheritance trees (3+) = refactoring needed. Use composition.

---

## Function Rules

| Rule | Description |
|------|-------------|
| **Small** | Max 20 lines, ideally 5-10 |
| **One Thing** | Does one thing, does it well |
| **One Level** | One level of abstraction per function |
| **Few Args** | Max 3 arguments, prefer 0-2 |
| **No Side Effects** | Don't mutate inputs unexpectedly |

---

## Code Structure

| Pattern | Apply |
|---------|-------|
| **Guard Clauses** | Early returns for edge cases |
| **Flat > Nested** | Avoid deep nesting (max 2 levels) |
| **Composition** | Small functions composed together |
| **Colocation** | Keep related code close |

---

## AI Coding Style

| Situation | Action |
|-----------|--------|
| User asks for feature | Write it directly |
| User reports bug | Fix it, don't explain |
| No clear requirement | Ask, don't assume |

---

## Anti-Patterns (DON'T)

| ❌ Pattern | ✅ Fix |
|-----------|-------|
| Comment every line | Delete obvious comments |
| Helper for one-liner | Inline the code |
| Factory for 2 objects | Direct instantiation |
| utils.ts with 1 function | Put code where used |
| "First we import..." | Just write code |
| Deep nesting | Guard clauses |
| Magic numbers | Named constants |
| God functions | Split by responsibility |

---

## 🔴 Before Editing ANY File (THINK FIRST!)

**Before changing a file, ask yourself:**

| Question | Why |
|----------|-----|
| **What imports this file?** | They might break |
| **What does this file import?** | Interface changes |
| **What tests cover this?** | Tests might fail |
| **Is this a shared component?** | Multiple places affected |

**Quick Check:**
```
File to edit: UserService.ts
└── Who imports this? → UserController.ts, AuthController.ts
└── Do they need changes too? → Check function signatures
```

> 🔴 **Rule:** Edit the file + all dependent files in the SAME task.
> 🔴 **Never leave broken imports or missing updates.**

---

## Connection Mapping (Trace Before You Touch)

Before writing a single line, build a mental model of the affected surface area.

**Layers to trace:**

```
Change target: calculateDiscount(order)
│
├── CALLERS  → Who calls this? (OrderService, CheckoutController, tests)
├── DEPS     → What does this call? (PricingRules, TaxEngine)
├── TYPES    → What types does it consume/produce? (Order → DiscountResult)
├── TESTS    → Which test files cover this? (order.test.ts, checkout.spec.ts)
└── SIDE FX  → Does it write to DB / emit events / mutate shared state?
```

**Signals that demand extra tracing:**

| Signal | Action |
|--------|--------|
| Function is exported | Search all import sites |
| Type/interface changes | Find every consumer of that shape |
| DB schema touched | Check migrations + all queries using that table |
| Event emitted | Find all listeners/subscribers |
| Env variable added/renamed | Update `.env.example`, CI config, docs |

> **Rule:** If tracing reveals more than 3 affected files, state the full impact list before editing.

---

## Thinking Model (Reason Before You Code)

Apply this mental sequence when facing any non-trivial task:

1. **Restate the goal** — What outcome does the user actually need? (not what they literally typed)
2. **Identify constraints** — Performance budget? Breaking change? Must stay backward compatible?
3. **Find the simplest path** — What is the minimum change that satisfies the goal?
4. **Spot the risks** — What can break? Where are the edge cases?
5. **Code** — Only now write or edit.
6. **Verify** — Does the output match step 1?

**Heuristics for hard problems:**

| Stuck on... | Try this |
|-------------|----------|
| Where does the bug come from? | Follow the data: input → transform → output. Find where it diverges from expected. |
| Which file to edit? | Find the place where the *behaviour* lives, not just where a type is defined. |
| Design feels wrong? | If you can't name the function cleanly, the responsibility boundary is wrong — split it. |
| Too many arguments? | The caller knows too much about internals — introduce an abstraction or group args into an object. |
| Circular dependency? | One of the two modules has two responsibilities — extract the shared piece. |

> **Rule:** If you cannot explain what a function does in one sentence, it does too much.

---

## Testing Standards

Write tests that document behaviour, not implementation.

**Test anatomy — AAA pattern:**

```
Arrange  → set up inputs and dependencies
Act      → call the unit under test (one call)
Assert   → verify the output/state (one concept per test)
```

**What to test:**

| Test this | Skip this |
|-----------|-----------|
| Public API / exported functions | Private helpers |
| Edge cases (null, empty, overflow) | Internal variable names |
| Error paths and thrown exceptions | Framework internals |
| Boundary values | Obvious pass-throughs |

**Naming convention:**

```
[unit]_[scenario]_[expectedResult]
getUserById_withInvalidId_throwsNotFound
calculateTax_forExemptItem_returnsZero
```

**Test quality rules:**

| Rule | Reason |
|------|--------|
| One assertion concept per test | Failures pinpoint exactly what broke |
| No logic in tests (no loops/ifs) | Tests must be obviously correct |
| Tests must be deterministic | Flaky tests are worse than no tests |
| Mock at the boundary, not inside | Test real logic; mock I/O and external services |
| Tests should run in isolation | No shared mutable state between tests |

**During a coding session — testing checklist:**

- [ ] Does the new function have at least one happy-path test?
- [ ] Is there a test for the main failure case?
- [ ] Did I run the affected test file before calling the task done?
- [ ] If I changed a public API, did I update existing tests (not just add new ones)?

> **Rule:** A feature without a failing test first isn't TDD — but a feature with no test at all is a liability.

---

## Summary

| Do | Don't |
|----|-------|
| Write code directly | Write tutorials |
| Let code self-document | Add obvious comments |
| Fix bugs immediately | Explain the fix first |
| Inline small things | Create unnecessary files |
| Name things clearly | Use abbreviations |
| Keep functions small | Write 100+ line functions |

> **Remember: The user wants working code, not a programming lesson.**

---

## 🔴 Self-Check Before Completing (MANDATORY)

**Before saying "task complete", verify:**

| Check | Question |
|-------|----------|
| ✅ **Goal met?** | Did I do exactly what user asked? |
| ✅ **Files edited?** | Did I modify all necessary files? |
| ✅ **Code works?** | Did I test/verify the change? |
| ✅ **No errors?** | Lint and TypeScript pass? |
| ✅ **Nothing forgotten?** | Any edge cases missed? |

> 🔴 **Rule:** If ANY check fails, fix it before completing.
