# Vridhi.ai — Phase 1 UI/UX Design Master Prompt

## ROLE

Act as a world-class:

* Product Designer
* UX Architect
* UI Designer
* Design System Designer
* Enterprise SaaS Designer
* UX Researcher
* Indian B2B SaaS Product Designer

Design the complete **Phase 1 production-ready UI/UX for Vridhi.ai**, an India-first AI business knowledge assistant.

The design must feel like a **real enterprise product that Indian businesses would trust and pay for**, not a flashy AI startup landing page.

---

# 1. PRODUCT

## Product Name

**Vridhi.ai**

## Product Description

Vridhi helps businesses search and understand their company information.

Users can:

* Connect Google Drive
* Connect Gmail
* Upload documents
* Search company information
* Ask AI questions
* Receive answers grounded in company data
* View citations and source documents
* Invite employees
* Manage connections
* Monitor synchronization
* View usage
* View audit activity

The Phase 1 objective is:

> **Get a real Indian business from signup → connection → first useful AI answer as quickly and confidently as possible.**

---

# 2. DESIGN PHILOSOPHY

The UI should feel:

### Trustworthy

The user is giving Vridhi access to company documents and emails.

### Professional

Suitable for:

* business owners
* CXOs
* managers
* operations teams
* sales teams
* finance teams
* HR teams

### Familiar

The interface should be easy for Indian users who are already familiar with:

* Google Workspace
* Microsoft Office
* Zoho
* Tally
* modern banking applications
* enterprise dashboards

### Premium but restrained

Do not make it look cheap.

Do not make it look like a flashy AI startup.

The visual language should communicate:

> **“This is serious business software.”**

---

# 3. IMPORTANT DESIGN RULE

## DO NOT DESIGN IT LIKE A TYPICAL AI STARTUP

Avoid:

* excessive gradients
* neon colors
* glowing AI effects
* purple/blue futuristic gradients
* glassmorphism everywhere
* huge rounded cards
* excessive animations
* floating blobs
* excessive illustrations
* dark futuristic dashboards
* “AI magic” visual effects

The product should not look like a gaming application.

---

# 4. INDIAN B2B DESIGN DIRECTION

Create a visual language that feels appropriate for Indian businesses.

Think:

**Modern Indian enterprise software**

not:

**American AI startup landing page.**

The design should work equally well for:

* a 30-person Indian company
* a 200-person company
* a 1,000-person company

Use familiar patterns from successful Indian business products while creating an original identity.

Do not copy any existing company's design.

---

# 5. COLOR SYSTEM

## Primary Color

Use a sophisticated **deep teal / green** as the primary brand color.

Suggested direction:

```text
Primary:
#176B5B
```

This should communicate:

* growth
* trust
* stability
* business
* India
* intelligence

Avoid overly bright green.

---

## Secondary Color

Use a restrained warm saffron/gold accent:

```text
Accent:
#C88A24
```

Use this VERY sparingly.

Examples:

* important highlights
* premium indicators
* selected status
* small brand details

Do NOT use saffron everywhere.

The product should not look politically or culturally themed.

---

## Background

Use a warm-neutral background rather than pure white.

```text
Background:
#F7F7F4
```

This gives the interface a slightly warmer, more comfortable feel.

---

## Surface

```text
Card:
#FFFFFF
```

---

## Primary Text

```text
#202624
```

---

## Secondary Text

```text
#66716D
```

---

## Borders

```text
#E3E7E4
```

---

## Success

Use a natural green.

```text
#2E7D5B
```

---

## Warning

Use muted amber.

```text
#B7791F
```

---

## Error

Use restrained red.

```text
#B94A48
```

Do not use extremely saturated colors.

---

# 6. COLOR RATIO

Use approximately:

```text
70% neutral background/surfaces
20% dark text
8% primary teal
2% accent color
```

The brand color should support the interface—not dominate it.

---

# 7. TYPOGRAPHY

Use a clean modern sans-serif.

Preferred:

**Inter**

Alternative:

**Manrope**

Typography should feel:

* highly readable
* professional
* compact
* business-oriented

Avoid oversized typography.

---

# 8. DESIGN LANGUAGE

Use:

* 8px spacing system
* subtle borders
* 6–10px corner radius
* restrained shadows
* compact cards
* clear hierarchy
* generous whitespace
* strong alignment

Avoid:

* giant rounded containers
* excessive 20–30px corner radius
* heavy shadows
* excessive cards inside cards

---

# 9. DESKTOP-FIRST

Primary product experience should be desktop-first.

Target:

```text
1440px
1280px
1024px
```

Also provide responsive behavior for:

```text
768px
390px
```

Mobile does not need to have every admin feature initially.

---

# 10. APPLICATION STRUCTURE

Create the following application structure:

```text
Vridhi
│
├── Dashboard
├── Ask Vridhi
├── Search
├── Knowledge
├── Connections
├── Team
├── Usage
├── Audit
└── Settings
```

Use a persistent left sidebar.

---

# 11. SIDEBAR

Design a compact professional sidebar.

Top:

```text
Vridhi.ai logo
```

Navigation:

```text
⌂ Dashboard

✦ Ask Vridhi

⌕ Search

▣ Knowledge

↔ Connections

◉ Team

▥ Usage

◷ Audit
```

Bottom:

```text
Help
Settings

User Avatar
Vivek
Admin
```

The sidebar should be approximately:

**240px wide.**

Do not make it oversized.

---

# 12. BRAND LOGO

Design a simple Vridhi.ai wordmark.

The identity should communicate:

* growth
* intelligence
* knowledge
* trust

Avoid:

* robot heads
* brains
* circuit boards
* generic AI spark icons

A subtle abstract growth/knowledge mark is preferred.

---

# 13. LOGIN SCREEN

Design:

```text
                 Vridhi.ai

        Intelligence for your business

        ┌─────────────────────────┐
        │ Continue with Google    │
        └─────────────────────────┘

        ─────── or ───────

        Work email
        [________________]

        Password
        [________________]

        [ Sign in ]

        Forgot password?

        Don't have an account?
        Create account
```

The page should feel trustworthy and minimal.

Do not add unnecessary marketing copy.

---

# 14. SIGNUP

Fields:

```text
Full name
Work email
Company name
Password
```

Then:

```text
Create Vridhi workspace
```

Keep signup extremely short.

---

# 15. ONBOARDING

This is the most important Phase-1 UX.

Create a guided onboarding flow.

## Step 1

```text
Welcome to Vridhi

Let's connect your business knowledge.

[Continue]
```

---

## Step 2

```text
What would you like to connect?

Google Drive
Your documents and company files

Gmail
Company email and conversations

Upload files
PDF, DOCX, XLSX, PPTX, CSV
```

Each should be a simple card.

---

## Step 3

Connection state:

```text
Connecting to Google Drive...

Securely authenticating
Discovering files
```

---

## Step 4

Sync progress:

```text
Your knowledge is being prepared

12,842 documents found

8,421 processed
4,421 remaining

████████████░░░░

You can continue using Vridhi.
```

Do not force users to stare at a progress bar.

Allow:

**Continue to Vridhi**

---

# 16. DASHBOARD

The dashboard should answer:

> **“What can I do right now?”**

Header:

```text
Good morning, Vivek

Here's what's happening with your workspace.
```

Main CTA:

```text
Ask Vridhi
```

Large but not enormous.

---

## Dashboard cards

### Knowledge

```text
12,842
Documents

3
Connected sources

Last synced
5 min ago
```

### Usage

```text
1,284
Questions this month

42
Active users
```

### Connections

```text
Google Drive     Healthy
Gmail            Healthy
```

---

# 17. DASHBOARD — IMPORTANT SECTION

Add:

## Ask Vridhi

Example:

```text
What are our payment terms for enterprise customers?
```

[Ask Vridhi]

This should be the main product action.

---

# 18. DASHBOARD — RECENT ACTIVITY

Show:

```text
Recent activity

Rahul asked:
"What is our leave policy?"

Priya connected:
Google Drive

Sync completed:
12,842 documents

Anita asked:
"Which customers have overdue invoices?"
```

Keep this compact.

---

# 19. ASK VRIDHI

This is the core screen.

Layout:

```text
┌──────────────────────────────────────────────┐
│ Ask Vridhi                                   │
│                                              │
│ Ask anything about your business             │
│                                              │
│ ┌──────────────────────────────────────────┐ │
│ │ What is our refund policy for enterprise │ │
│ │ customers?                               │ │
│ │                                      ↑   │ │
│ └──────────────────────────────────────────┘ │
│                                              │
│ Suggested questions                          │
│                                              │
│ What is our leave policy?                    │
│ What are our payment terms?                  │
│ Who manages enterprise accounts?             │
└──────────────────────────────────────────────┘
```

---

# 20. AI ANSWER DESIGN

The answer must look like a business answer—not ChatGPT clone UI.

Example:

```text
Enterprise customers can request a refund
within 30 days of purchase, subject to the
conditions outlined in the enterprise agreement.
```

Then:

### Sources

```text
Sources

1. Enterprise Refund Policy
   Page 4
   Updated 12 Aug 2026

2. Customer Agreement
   Section 7
```

---

# 21. CITATION DESIGN

Make citations clickable.

When clicked:

```text
Right-side source panel
```

Show:

```text
Enterprise Refund Policy.pdf

Page 4

"...refund requests must be submitted
within thirty days..."
```

Highlight the relevant passage.

This is extremely important for trust.

---

# 22. SOURCE PANEL

Create a 360–420px right-side drawer.

Structure:

```text
Source

Enterprise Refund Policy

PDF

Page 4 of 18

Relevant passage

────────────────

Document details

Updated
12 Aug 2026

Source
Google Drive

Open original
```

---

# 23. “NO ANSWER” STATE

If information cannot be found:

```text
I couldn't find enough information in your
connected sources to answer this confidently.

You can try:

• Asking the question differently
• Connecting another source
• Uploading the relevant document
```

Do NOT display a fabricated answer.

---

# 24. SEARCH PAGE

Create conventional enterprise search.

Search box:

```text
Search your company knowledge...
```

Filters:

```text
Source
Document type
Date
Owner
```

Results:

```text
Enterprise Pricing Policy
Google Drive
Updated 3 days ago

Relevant passage...
```

---

# 25. KNOWLEDGE PAGE

Show all indexed knowledge.

Table:

```text
Document
Source
Type
Last Updated
Status
```

Example:

```text
Employee Handbook
Google Drive
PDF
Yesterday
Indexed

Pricing 2026
Google Drive
DOCX
2 days ago
Indexed
```

---

# 26. CONNECTIONS PAGE

This should be one of the strongest screens.

Header:

```text
Connections

Connect the systems your business already uses.
```

Cards:

```text
Google Drive
Connected
12,842 documents
Last sync 5 min ago

[View]

Gmail
Connected
28,421 emails
Last sync 8 min ago

[View]

Upload Files
Add documents manually

[Upload]
```

---

# 27. CONNECTION DETAIL

Show:

```text
Google Drive

● Connected

Last sync
5 minutes ago

Documents
12,842

Processed
12,721

Failed
121

[Sync now]
```

Then:

```text
Sync history

Today 14:30
Completed
12,842 documents

Today 09:10
Completed
```

---

# 28. ERROR STATE

If connection fails:

```text
Google Drive

● Connection needs attention

We couldn't refresh your connection.

[Reconnect]

Last successful sync
Today, 10:32 AM
```

Never show technical AWS errors to customers.

---

# 29. TEAM PAGE

Table:

```text
Name
Email
Role
Status
Last active
```

Roles:

```text
Owner
Admin
Member
```

CTA:

**Invite member**

---

# 30. INVITATION MODAL

```text
Invite teammates

Work email
[________________________]

Role
[Member ▼]

[Send invitation]
```

Simple.

---

# 31. USAGE PAGE

Phase 1 should be simple.

Show:

```text
This month

AI questions
1,284

Active users
42

Documents
12,842

Connected sources
3
```

Do not create a complex BI dashboard.

---

# 32. AUDIT PAGE

Simple enterprise table:

```text
Time
User
Activity
Resource

14:32
Rahul
Asked a question
Company knowledge

14:28
Priya
Connected Google Drive

14:12
Anita
Invited user
```

---

# 33. SETTINGS

Sections:

```text
Organization
Profile
Team
Security
Connections
Notifications
Billing
```

Only show settings that actually work in Phase 1.

Do not create fake enterprise settings.

---

# 34. MOBILE

Mobile should prioritize:

```text
Dashboard
Ask Vridhi
Search
Connections
Profile
```

Use bottom navigation or compact navigation.

Do not attempt to reproduce the entire desktop admin experience.

---

# 35. COMPONENT LIBRARY

Create a reusable design system.

Components:

```text
Button
Input
Textarea
Select
Dropdown
Modal
Drawer
Toast
Badge
Avatar
Tooltip
Tabs
Table
Card
Progress
Skeleton
Empty State
Error State
Search Box
Source Citation
AI Answer
Connection Card
```

Each component must have:

* default
* hover
* active
* disabled
* loading
* error

states where applicable.

---

# 36. BUTTON SYSTEM

Primary:

```text
Teal background
White text
```

Secondary:

```text
White background
Dark border
```

Tertiary:

```text
Text button
```

Danger:

```text
Muted red
```

Avoid giant buttons.

---

# 37. AI VISUAL LANGUAGE

The AI should not look magical.

Avoid:

✨ glowing AI
🤖 robot
🌌 futuristic gradients

Instead use:

```text
Vridhi
```

with a small understated intelligence indicator.

The product should communicate:

> **Useful intelligence, not artificial magic.**

---

# 38. MICROCOPY

Use simple Indian business English.

Examples:

Instead of:

> “Initialize your enterprise knowledge ingestion pipeline.”

Say:

> **Connect your business data**

Instead of:

> “Execute semantic retrieval.”

Say:

> **Search your company knowledge**

Instead of:

> “No context was retrieved.”

Say:

> **We couldn't find enough information to answer this.**

Instead of:

> “Authentication token invalid.”

Say:

> **Your connection needs to be renewed.**

---

# 39. INDIAN USER UX

Use:

* ₹ where money is shown
* Indian date formats where appropriate
* IST timezone by default
* Indian phone format
* lakhs/crores where appropriate in business contexts

Example:

```text
₹42.5 lakh
```

rather than:

```text
₹4,250,000
```

when presenting business summaries to Indian users.

Do not overdo cultural elements.

---

# 40. DESIGN FOR TRUST

Whenever Vridhi uses company information, communicate:

```text
Source
Updated
Permission
Confidence
```

The interface should make users feel:

> “I can verify this.”

not:

> “I hope the AI is correct.”

---

# 41. RESPONSIBLE AI UX

For important answers display:

```text
Based on 3 sources
```

Optionally:

```text
Confidence: High
```

Do not present confidence scores if they are not backed by a meaningful calibrated methodology.

Avoid fake precision such as:

```text
97.42% confidence
```

---

# 42. EMPTY STATES

Every empty state must answer:

1. What is missing?
2. Why does it matter?
3. What should I do?

Example:

```text
No connections yet

Connect your business systems so Vridhi
can answer questions using your company data.

[Connect Google Drive]
```

---

# 43. LOADING EXPERIENCE

Use skeleton loaders for:

* dashboards
* search results
* tables

For AI responses use progressive status:

```text
Searching your company knowledge...
Finding relevant sources...
Preparing your answer...
```

Do not fake detailed chain-of-thought.

---

# 44. RESPONSIVE BEHAVIOR

Desktop:

```text
Sidebar + Main + Optional Source Panel
```

Tablet:

```text
Collapsed Sidebar + Main
```

Mobile:

```text
Header
Main
Bottom Navigation
```

Source panel becomes a bottom sheet on mobile.

---

# 45. ACCESSIBILITY

Must support:

* keyboard navigation
* visible focus states
* sufficient contrast
* screen reader labels
* semantic HTML
* accessible forms
* accessible tables
* error messages associated with fields

---

# 46. DESIGN DELIVERABLES

Create the following Figma/design screens:

## Authentication

1. Login
2. Signup
3. Forgot password
4. Reset password

## Onboarding

5. Welcome
6. Connect sources
7. Connecting
8. Syncing
9. Ready

## Application

10. Dashboard
11. Ask Vridhi
12. AI Answer
13. AI Answer + Source Drawer
14. Search
15. Search Results
16. Knowledge
17. Document Preview

## Connections

18. Connections
19. Connection Detail
20. Connection Error
21. Sync History

## Team

22. Team
23. Invite User
24. User Details

## Administration

25. Usage
26. Audit
27. Settings

## System States

28. Empty
29. Loading
30. Error
31. Permission denied
32. No answer
33. Connection failed
34. Sync failed

---

# 47. PROTOTYPE FLOW

Create a clickable prototype for this exact journey:

```text
Login
 ↓
Dashboard
 ↓
Connect Google Drive
 ↓
OAuth
 ↓
Sync
 ↓
Dashboard
 ↓
Ask Vridhi
 ↓
Question
 ↓
AI Answer
 ↓
Click Citation
 ↓
Source Drawer
 ↓
Verify Evidence
```

This is the most important Phase-1 prototype.

---

# 48. SALES DEMO FLOW

Create a polished demo environment.

Demo dashboard should already contain realistic fictional company data.

Example:

**Acme Manufacturing India Pvt. Ltd.**

Documents:

* Employee Handbook
* Sales Policy
* Pricing Policy
* Customer Agreement
* Procurement SOP
* Travel Policy
* Refund Policy
* Product Documentation

Questions:

```text
What is our enterprise refund policy?

Which customers have payment terms above 60 days?

What is our employee travel policy?

Who owns enterprise accounts?

What are our standard payment terms?
```

The demo should demonstrate:

```text
Question
 ↓
Answer
 ↓
Evidence
 ↓
Source
```

within seconds.

---

# 49. DESIGN QUALITY BAR

The final UI should feel closer to:

**serious enterprise SaaS**

than:

**AI startup landing page.**

Visual characteristics:

```text
Clean
Calm
Professional
Trustworthy
Fast
Dense enough for business
Not cluttered
Not flashy
```

---

# 50. FINAL DESIGN PRINCIPLE

The most important screen is not the dashboard.

It is:

# **Ask Vridhi**

The user should open Vridhi and immediately understand:

> **“I can ask Vridhi about my business.”**

Everything else supports that experience.

---

# 51. FINAL VISUAL DIRECTION

Use this overall aesthetic:

```text
Warm off-white background
        +
White surfaces
        +
Deep teal primary
        +
Very subtle saffron/gold accents
        +
Dark charcoal typography
        +
Thin borders
        +
Small shadows
        +
Moderate corner radius
        +
Clean data tables
        +
Excellent whitespace
```

The resulting product should look:

> **Indian, professional, trustworthy, modern, premium and practical.**

Not:

> **flashy, futuristic, neon, over-designed or “AI gimmicky.”**

---

# 52. FINAL OUTPUT

Produce:

1. Complete desktop UI
2. Responsive tablet UI
3. Mobile UI
4. Design system
5. Color tokens
6. Typography system
7. Component library
8. All Phase-1 screens
9. Empty/loading/error states
10. Clickable prototype
11. Sales demo workspace
12. Developer-ready design specifications

Every screen should include:

* spacing
* typography
* colors
* component states
* interaction behavior
* responsive behavior
* accessibility notes

The final result must be **implementation-ready**, not just visual concept art.

The product must look like something a real Indian company could confidently deploy internally tomorrow.
