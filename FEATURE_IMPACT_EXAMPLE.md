# Feature Impact Analysis Example

## Overview

Feature Impact Analysis combines technical impact (from code-graph-mcp) with feature-level context (from project.md).

## Example Scenario

### project.md

```markdown
## Feature: Authentication

Handles user authentication and session management.

### Decisions
- AUTH-001: Use JWT tokens for stateless authentication (because: scales horizontally)
  - Constraint: Token expiry must be < 24 hours
  - Constraint: Refresh tokens stored in httpOnly cookies
- AUTH-002: Password hashing with bcrypt (because: industry standard)
  - Constraint: Minimum 12 rounds
  - Constraint: Must not store plain text passwords

### Files
- src/auth/**
- src/middleware/auth*

### Dependencies
- Feature: User Management

### Constraints
- All auth endpoints must return within 200ms
- Rate limiting: 5 attempts per minute
```

### Code Change

Developer wants to change the `authenticate_user` function.

### API Call

```bash
curl http://localhost:17318/feature/trellis/impact/authenticate_user
```

### Response

```json
{
  "symbol": "authenticate_user",
  "technical_impact": {
    "risk_level": "HIGH",
    "affected_functions": [
      {"name": "login", "file_path": "src/auth/login.py"},
      {"name": "refresh_token", "file_path": "src/auth/token.py"},
      {"name": "logout", "file_path": "src/auth/session.py"}
    ]
  },
  "feature_impacts": [
    {
      "feature_name": "Authentication",
      "impacted_functions": [
        {"name": "authenticate_user", "file_path": "src/auth/auth.py", "is_root": true},
        {"name": "login", "file_path": "src/auth/login.py"},
        {"name": "refresh_token", "file_path": "src/auth/token.py"},
        {"name": "logout", "file_path": "src/auth/session.py"}
      ],
      "affected_decisions": [
        {
          "id": "AUTH-001",
          "description": "Use JWT tokens for stateless authentication"
        },
        {
          "id": "AUTH-002",
          "description": "Password hashing with bcrypt"
        }
      ],
      "risk_flags": [
        "Root function change affects 4 functions",
        "Feature has 1 dependencies: User Management"
      ],
      "recommended_actions": [
        "Review affected decisions in project.md before committing changes"
      ]
    }
  ],
  "divergence_warnings": [],
  "development_pointers": [
    "Feature: Authentication - Handles user authentication and session management.",
    "Constraint: All auth endpoints must return within 200ms",
    "Constraint: Rate limiting: 5 attempts per minute",
    "Decision AUTH-001: Use JWT tokens for stateless authentication",
    "  Why: scales horizontally",
    "  Must maintain: Token expiry must be < 24 hours",
    "  Must maintain: Refresh tokens stored in httpOnly cookies",
    "Decision AUTH-002: Password hashing with bcrypt",
    "  Why: industry standard",
    "  Must maintain: Minimum 12 rounds",
    "  Must maintain: Must not store plain text passwords",
    "Dependencies: User Management"
  ],
  "summary": "Feature Impact Analysis: authenticate_user\n\nAffects 4 functions across 1 features\n\n📦 Feature: Authentication\n   Functions: 4\n   ⚠️  Affected decisions: 2\n   ⚠️  Root function change affects 4 functions\n   ⚠️  Feature has 1 dependencies: User Management\n   💡 Review affected decisions in project.md before committing changes"
}
```

## What This Tells the Coding Agent

1. **Technical Impact**: "Changing this affects 3 other functions"
2. **Feature Context**: "This is part of Authentication feature"
3. **Decisions**: "AUTH-001 says use JWT tokens, AUTH-002 says use bcrypt"
4. **Constraints**: "Must be <200ms, must use httpOnly cookies"
5. **Dependencies**: "User Management depends on this"
6. **Actions**: "Review decisions before committing"

## Key Benefits

### For Coding Agents
- Understands not just WHAT changed, but WHY decisions were made
- Gets constraints that must be maintained
- Knows which features are affected

### For Developers
- Prevents feature divergence (violating past decisions)
- Maintains architectural consistency
- Tracks feature boundaries

### For Teams
- Feature ownership is clear
- Dependencies are visible
- Constraints are documented and enforced

## API Endpoints

### Get Feature Impact Report
```bash
GET /feature/{project_id}/impact/{symbol}?depth=2
```

### Get Feature Context
```bash
GET /feature/{project_id}/context/{symbol}
```

### Get Development Pointers
```bash
GET /feature/{project_id}/pointers/{symbol}
```

### Check Divergence
```bash
GET /feature/{project_id}/divergence/{symbol}
```

## project.md Format

```markdown
## Feature: Feature Name

Description of feature.

### Decisions
- DEC-001: Decision description (because: rationale)
  - Constraint: Must maintain X
  - Constraint: Must not do Y

### Files
- src/feature/**
- src/middleware/feature*

### Dependencies
- Feature: Other Feature

### Constraints
- Global constraint 1
- Global constraint 2
```

## File to Feature Mapping

Functions are mapped to features based on file path patterns:

| File Path | Feature |
|-----------|---------|
| `src/auth/login.py` | Authentication |
| `src/payments/stripe.py` | Payment Processing |
| `src/users/profile.py` | User Management |
| `src/export/csv.py` | Data Export |

## Integration with code-graph-mcp

Trellis leverages code-graph-mcp for:
- **AST parsing** - Finding all functions
- **Call graph** - Finding affected functions
- **Impact analysis** - Risk scoring
- **Semantic search** - Finding related code

Trellis adds on top:
- **Feature grouping** - Organizing by feature
- **Decision tracking** - Why choices were made
- **Constraint checking** - What must be maintained
- **Development guidance** - Actionable pointers
