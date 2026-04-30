# Trellis Project Specification

## Overview

Trellis is a Python-native code graph workflow layer built on top of code-graph-mcp.

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

---

## Feature: Payment Processing

Handles payment transactions and refunds.

### Decisions
- PAY-001: Use Stripe for payment processing (because: PCI compliance handled by Stripe)
  - Constraint: Must validate webhook signatures
  - Constraint: Idempotency keys required for all transactions
- PAY-002: Async processing for webhooks (because: reliability)
  - Constraint: Must retry failed webhooks up to 3 times

### Files
- src/payments/**
- src/webhooks/payment*

### Dependencies
- Feature: Authentication
- Feature: User Management

### Constraints
- Must not store credit card numbers
- All transactions logged for audit

---

## Feature: User Management

Manages user profiles, roles, and permissions.

### Decisions
- USER-001: Soft deletes only (because: data recovery)
  - Constraint: Deleted users marked with deleted_at timestamp
- USER-002: Role-based access control (because: flexibility)
  - Constraint: Admin role cannot be self-assigned

### Files
- src/users/**
- src/permissions/**

### Dependencies
- None

---

## Feature: Data Export

Allows users to export their data.

### Status: deprecated

### Decisions
- EXP-001: CSV format only (because: universal compatibility)

### Files
- src/export/**

### Dependencies
- Feature: User Management
