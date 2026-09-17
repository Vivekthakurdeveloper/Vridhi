<!-- Page 1 -->

Highwatch — Google Workspace Enterprise Connector \| Phase 1 Page 1
Highwatch — Google Workspace Enterprise Connector Enterprise Setup &
Requirements Document — Phase 1

1.  Purpose Highwatch connects to a company's Google Workspace so
    employees can securely search and ask questions about company
    information stored in:  Gmail  Google Drive  Shared Drives  Google
    Chat Highwatch respects the company's existing Google permissions. A
    user can only search information that they are already authorized to
    access.
2.  What the Enterprise Needs to Provide Google Workspace Admin A Google
    Workspace administrator must authorize Highwatch. The administrator
    should have permission to configure Google Workspace / Google Cloud
    access for the organization. Google Cloud Project The customer
    creates or provides a Google Cloud project for the integration.
    Highwatch requires the relevant Google APIs to be enabled. Service
    Account / Enterprise Authorization For organization-wide access, the
    customer configures the required Google enterprise authorization
    mechanism, such as Domain-Wide Delegation, where applicable.
    Highwatch provides the exact setup instructions and required scopes.
3.  Google Data Highwatch Will Access Gmail Highwatch can access: 
    Emails  Email threads  Sender/recipient information  Email body 
    Attachments  Relevant metadata Google Drive Highwatch can access: 
    Google Docs  Google Sheets  Google Slides  PDFs

------------------------------------------------------------------------

<!-- Page 2 -->

Highwatch — Google Workspace Enterprise Connector \| Phase 1 Page 2 
DOC/DOCX  XLS/XLSX  PPT/PPTX  Other supported files  Folder information 
Shared Drive files Google Chat Highwatch can access:  Chat spaces 
Messages  Threads/replies  Message metadata  Supported attachments 4.
Permissions & Security This is the most important enterprise
requirement. Highwatch must preserve Google Workspace permissions.
Example: Google Drive Financial Strategy.pdf CEO → Access Finance →
Access Employee → No Access If an employee asks: "What is in the
financial strategy document?" Highwatch must not return the document to
that employee. The authorization flow is: User ↓ Highwatch ↓ Search ↓
Permission Check ↓ Authorized Documents Only ↓ AI Answer Highwatch must
never send unauthorized document content to the LLM. 5. Google Groups If
Google permissions are granted through groups, Highwatch needs to
understand group membership. Example:

------------------------------------------------------------------------

<!-- Page 3 -->

Highwatch — Google Workspace Enterprise Connector \| Phase 1 Page 3
<finance@company.com> ↓ A B C If a document is available to
<finance@company.com>, Highwatch should determine whether the requesting
user belongs to that group. 6. Initial Sync After authorization: Google
Workspace ↓ Initial Sync ↓ Gmail / Drive / Chat ↓ Highwatch ↓ Index ↓
Search Ready Highwatch should show: Connection: Connected Sync: In
Progress Files processed: 25,420 Emails processed: 82,100 Chat messages:
14,200 Status: Processing 7. Continuous Sync After the initial sync,
Highwatch should only process changes. New email ↓ Highwatch Updated
document ↓ Highwatch Deleted document ↓ Highwatch Customers should not
have to manually upload their data every time. 8. Deletion If a document
is deleted from Google Workspace: Google ↓ Deleted ↓ Highwatch detects
deletion ↓ Highwatch removes/inactivates indexed content

------------------------------------------------------------------------

<!-- Page 4 -->

Highwatch — Google Workspace Enterprise Connector \| Phase 1 Page 4 The
deleted content must no longer appear in search or AI answers. 9.
Supported Files For Phase 1: Google Docs Google Sheets Google Slides PDF
DOC/DOCX XLS/XLSX PPT/PPTX TXT CSV ZIP If an enterprise uploads a ZIP:
documents.zip ↓ Extract ↓ Supported files ↓ Index individual files ZIP
processing should have limits on size, number of files and nested
archives. 10. What Highwatch Stores Highwatch should store only what is
required for search, authorization and processing. Typical metadata:
Tenant ID Source Document ID Document title Content Owner Created date
Updated date Google URL Users with access Groups with access Source type
Document status Embeddings and searchable chunks are stored in
Highwatch's search infrastructure. 11. Enterprise Security Requirements
Highwatch should provide:  Encryption in transit  Encryption at rest 
Secure credential storage  Tenant isolation  Permission-aware search 
Audit logs

------------------------------------------------------------------------

<!-- Page 5 -->

Highwatch — Google Workspace Enterprise Connector \| Phase 1 Page 5 
Secure token/credential handling  Deletion propagation  No unauthorized
data exposure Highwatch must never expose Google credentials or access
tokens in application logs. 12. Customer Setup — Simple Version The
entire enterprise setup should ideally look like this: STEP 1 Admin
signs into Highwatch ↓ STEP 2 Click "Connect Google Workspace" ↓ STEP 3
Google Admin authorizes Highwatch ↓ STEP 4 Select: ■ Gmail ■ Google
Drive ■ Shared Drives ■ Google Chat ↓ STEP 5 Highwatch starts initial
sync ↓ STEP 6 Permissions are synchronized ↓ STEP 7 Employees can search
and ask questions 13. Minimum Enterprise Requirements Requirement
Required Google Workspace organization ✓ Google Workspace Admin ✓ Google
Cloud Project ✓ Required Google APIs ✓

------------------------------------------------------------------------

<!-- Page 6 -->

Highwatch — Google Workspace Enterprise Connector \| Phase 1 Page 6
Requirement Required Enterprise authorization / delegation ✓ Required
API scopes ✓ Highwatch authorization ✓ Gmail Optional Drive Optional
Shared Drives Optional Google Chat Optional 14. Highwatch Phase 1 Scope
Connect Google Workspace Sources Gmail Google Drive Shared Drives Google
Chat Core capabilities Authentication ↓ Initial Sync ↓ Incremental Sync
↓ Permission Sync ↓ Document Processing ↓ Search ↓ AI Q&A; ↓ Deletion
Handling Don't build Calendar, Meet, Forms, Sites, Tasks, Contacts, etc.
for the first enterprise release. 15. Enterprise Outcome Once connected,
an employee should simply be able to ask: "What did the sales team
discuss with Acme last month?" Highwatch searches authorized: Gmail +
Drive + Google Chat

------------------------------------------------------------------------

<!-- Page 7 -->

Highwatch — Google Workspace Enterprise Connector \| Phase 1 Page 7 and
provides an answer with citations back to the original Google sources.
That's the Phase 1 enterprise Google Workspace connector.
