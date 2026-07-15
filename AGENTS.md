# AGENTS

<skills_system priority="1">

## Available Skills

<!-- SKILLS_TABLE_START -->
<usage>
When users ask you to perform tasks, check if any of the available skills below can help complete the task more effectively. Skills provide specialized capabilities and domain knowledge.

How to use skills:
- Invoke: `npx openskills read <skill-name>` (run in your shell)
  - For multiple: `npx openskills read skill-one,skill-two`
- The skill content will load with detailed instructions on how to complete the task
- Base directory provided in output for resolving bundled resources (references/, scripts/, assets/)

Usage notes:
- Only use skills listed in <available_skills> below
- Do not invoke a skill that is already loaded in your context
- Each skill invocation is stateless
</usage>

<available_skills>

<skill>
<name>clean-code</name>
<description>Pragmatic coding standards - concise, direct, no over-engineering, no unnecessary comments</description>
<location>global</location>
</skill>

<skill>
<name>trellis-mcp</name>
<description>"Graph-based code analysis and knowledge management using Trellis MCP tools. Use when the user wants to: (1) analyze or understand a codebase structure, (2) assess impact of proposed changes, (3) trace dependencies between features or functions, (4) plan implementations with full context, (5) create or query linkable documentation, (6) verify changes after implementation. Triggers: 'analyze this codebase', 'what's the impact of changing X', 'help me understand this project', 'before I make changes', 'trace dependencies', 'impact analysis', 'feature graph', 'understand the architecture', 'how does X relate to Y', 'plan this refactor', 'create a note about', 'find documentation about'.</description>
<location>global</location>
</skill>

</available_skills>
<!-- SKILLS_TABLE_END -->

</skills_system>
