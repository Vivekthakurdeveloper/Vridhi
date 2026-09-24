from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlencode
from uuid import UUID, uuid4

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.config import Settings
from app.errors import AppError
from app.models import (
    Connection,
    ConnectionCredential,
    Document,
    Group,
    OrganizationMember,
    SyncJob,
    User,
)
from app.security import (
    ConnectionHealth,
    ConnectionStatus,
    DocumentStatus,
    DocumentVisibility,
    MemberStatus,
    SyncJobStatus,
    SyncJobType,
    generate_token,
    utcnow,
)
from app.services import autosync
from app.services.queue import IngestQueue
from app.services.storage import ObjectStorage
from app.services.tokens import TokenStore

logger = logging.getLogger(__name__)

MOCK_FOLDERS = [
    {"id": "folder-contracts", "name": "Contracts", "path": "My Drive / Contracts"},
    {"id": "folder-hr", "name": "HR Policies", "path": "My Drive / HR Policies"},
    {"id": "folder-finance", "name": "Finance", "path": "My Drive / Finance"},
    {
        "id": "folder-shared-legal",
        "name": "Legal",
        "path": "Legal (Shared Drive) / Legal",
        "drive_name": "Legal (Shared Drive)",
    },
]

MOCK_FILES = {
    "folder-contracts": [
        {
            "id": "file-msa-2024",
            "name": "Master Service Agreement 2024.txt",
            "mimeType": "text/plain",
            "webViewLink": "https://drive.google.com/file/d/file-msa-2024/view",
            "modifiedTime": "2024-11-01T10:00:00Z",
            "permissions": [{"type": "domain", "role": "reader"}, {"type": "user", "emailAddress": "owner@example.com", "role": "owner"}],
            "content": (
                "Master Service Agreement 2024.\n"
                "Vendor contracts renew automatically unless cancelled 30 days prior.\n"
                "Liability cap is INR 50,00,000 per incident.\n"
            ),
        },
        {
            # Phase I smoke fixture: a real multi-tab .xlsx (built with
            # openpyxl, base64-encoded) proving Task 3's multi-tab Sheets
            # support -- worker/pipeline/process.py's _parse_xlsx walks every
            # worksheet, not just the first, so text unique to the SECOND tab
            # ("Details") is indexed and searchable. Named/typed as a plain
            # .xlsx rather than a `google-apps.spreadsheet` fixture because
            # mock mode's _download_file_bytes (services/drive.py) returns
            # file_meta["mimeType"] as-is -- it doesn't perform the
            # export-mimeType translation that only the live Google export
            # path does -- so a fixture claiming the Google mime would be
            # misclassified by classify_document. A real spreadsheet's
            # multi-tab export is instead covered live (see ARCHITECTURE_NOTES.md).
            "id": "file-multitab-sheet",
            "name": "Quarterly Tabs.xlsx",
            "mimeType": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "webViewLink": "https://drive.google.com/file/d/file-multitab-sheet/view",
            "modifiedTime": "2025-04-01T09:00:00Z",
            "permissions": [{"type": "domain", "role": "reader"}],
            "content_b64": (
                "UEsDBBQAAAAIAIY8Nl1Gx01IlQAAAM0AAAAQAAAAZG9jUHJvcHMvYXBwLnhtbE3PTQvCMAwG4L9S"
                "dreZih6kDkQ9ip68zy51hbYpbYT67+0EP255ecgboi6JIia2mEXxLuRtMzLHDUDWI/o+y8qhiqHk"
                "e64x3YGMsRoPpB8eA8OibdeAhTEMOMzit7Dp1C5GZ3XPlkJ3sjpRJsPiWDQ6sScfq9wcChDneiU+"
                "ixNLOZcrBf+LU8sVU57mym/8ZAW/B7oXUEsDBBQAAAAIAIY8Nl1/Br9Y7gAAACsCAAARAAAAZG9j"
                "UHJvcHMvY29yZS54bWzNksFKxDAQhl9Fcm8nTaFi6Pay4klBcEHxFpLZ3WDThGSk3be3jbtdRB/A"
                "Y2b+fPMNTKuD1D7ic/QBI1lMN5PrhyR12LAjUZAASR/RqVTOiWFu7n10iuZnPEBQ+kMdEATnDTgk"
                "ZRQpWIBFWImsa42WOqIiH894o1d8+Ix9hhkN2KPDgRJUZQWsWyaG09S3cAUsMMLo0ncBzUrM1T+x"
                "uQPsnJySXVPjOJZjnXPzDhW8PT2+5HULOyRSg8b5V7KSTgE37DL5td7e7x5YJ7hoCn5XCLHjt7Ju"
                "ZFW/L64//K7Czhu7t//Y+CLYtfDrLrovUEsDBBQAAAAIAIY8Nl2ZXJwjEAYAAJwnAAATAAAAeGwv"
                "dGhlbWUvdGhlbWUxLnhtbO1aW3PaOBR+76/QeGf2bQvGNoG2tBNzaXbbtJmE7U4fhRFYjWx5ZJGE"
                "f79HNhDLlg3tkk26mzwELOn7zkVH5+g4efPuLmLohoiU8nhg2S/b1ru3L97gVzIkEUEwGaev8MAK"
                "pUxetVppAMM4fckTEsPcgosIS3gUy9Zc4FsaLyPW6rTb3VaEaWyhGEdkYH1eLGhA0FRRWm9fILTl"
                "HzP4FctUjWWjARNXQSa5iLTy+WzF/NrePmXP6TodMoFuMBtYIH/Ob6fkTlqI4VTCxMBqZz9Wa8fR"
                "0kiAgsl9lAW6Sfaj0xUIMg07Op1YznZ89sTtn4zK2nQ0bRrg4/F4OLbL0otwHATgUbuewp30bL+k"
                "QQm0o2nQZNj22q6RpqqNU0/T933f65tonAqNW0/Ta3fd046Jxq3QeA2+8U+Hw66JxqvQdOtpJif9"
                "rmuk6RZoQkbj63oSFbXlQNMgAFhwdtbM0gOWXin6dZQa2R273UFc8FjuOYkR/sbFBNZp0hmWNEZy"
                "nZAFDgA3xNFMUHyvQbaK4MKS0lyQ1s8ptVAaCJrIgfVHgiHF3K/99Ze7yaQzep19Os5rlH9pqwGn"
                "7bubz5P8c+jkn6eT101CznC8LAnx+yNbYYcnbjsTcjocZ0J8z/b2kaUlMs/v+QrrTjxnH1aWsF3P"
                "z+SejHIju932WH32T0duI9epwLMi15RGJEWfyC265BE4tUkNMhM/CJ2GmGpQHAKkCTGWoYb4tMas"
                "EeATfbe+CMjfjYj3q2+aPVehWEnahPgQRhrinHPmc9Fs+welRtH2Vbzco5dYFQGXGN80qjUsxdZ4"
                "lcDxrZw8HRMSzZQLBkGGlyQmEqk5fk1IE/4rpdr+nNNA8JQvJPpKkY9psyOndCbN6DMawUavG3WH"
                "aNI8ev4F+Zw1ChyRGx0CZxuzRiGEabvwHq8kjpqtwhErQj5iGTYacrUWgbZxqYRgWhLG0XhO0rQR"
                "/FmsNZM+YMjszZF1ztaRDhGSXjdCPmLOi5ARvx6GOEqa7aJxWAT9nl7DScHogstm/bh+htUzbCyO"
                "90fUF0rkDyanP+kyNAejmlkJvYRWap+qhzQ+qB4yCgXxuR4+5Xp4CjeWxrxQroJ7Af/R2jfCq/iC"
                "wDl/Ln3Ppe+59D2h0rc3I31nwdOLW95GblvE+64x2tc0LihjV3LNyMdUr5Mp2DmfwOz9aD6e8e36"
                "2SSEr5pZLSMWkEuBs0EkuPyLyvAqxAnoZFslCctU02U3ihKeQhtu6VP1SpXX5a+5KLg8W+Tpr6F0"
                "PizP+Txf57TNCzNDt3JL6raUvrUmOEr0scxwTh7LDDtnPJIdtnegHTX79l125COlMFOXQ7gaQr4D"
                "bbqd3Do4npiRuQrTUpBvw/npxXga4jnZBLl9mFdt59jR0fvnwVGwo+88lh3HiPKiIe6hhpjPw0OH"
                "eXtfmGeVxlA0FG1srCQsRrdguNfxLBTgZGAtoAeDr1EC8lJVYDFbxgMrkKJ8TIxF6HDnl1xf49GS"
                "49umZbVuryl3GW0iUjnCaZgTZ6vK3mWxwVUdz1Vb8rC+aj20FU7P/lmtyJ8MEU4WCxJIY5QXpkqi"
                "8xlTvucrScRVOL9FM7YSlxi84+bHcU5TuBJ2tg8CMrm7Oal6ZTFnpvLfLQwJLFuIWRLiTV3t1eeb"
                "nK56Inb6l3fBYPL9cMlHD+U751/0XUOufvbd4/pukztITJx5xREBdEUCI5UcBhYXMuRQ7pKQBhMB"
                "zZTJRPACgmSmHICY+gu98gy5KRXOrT45f0Usg4ZOXtIlEhSKsAwFIRdy4+/vk2p3jNf6LIFthFQy"
                "ZNUXykOJwT0zckPYVCXzrtomC4Xb4lTNuxq+JmBLw3punS0n/9te1D20Fz1G86OZ4B6zh3OberjC"
                "Raz/WNYe+TLfOXDbOt4DXuYTLEOkfsF9ioqAEativrqvT/klnDu0e/GBIJv81tuk9t3gDHzUq1ql"
                "ZCsRP0sHfB+SBmOMW/Q0X48UYq2msa3G2jEMeYBY8wyhZjjfh0WaGjPVi6w5jQpvQdVA5T/b1A1o"
                "9g00HJEFXjGZtjaj5E4KPNz+7w2wwsSO4e2LvwFQSwMEFAAAAAgAhjw2XU7gOK5uAQAAwwIAABgA"
                "AAB4bC93b3Jrc2hlZXRzL3NoZWV0MS54bWx1Ul1vwjAM/CtR3iEUiQ2hthJlmkBiEoON8RqoSyPy"
                "0SWGbv9+SYGKTeMpPud8ZzuJa2MPrgRA8qWkdgktEasRY25XguKuayrQ/qYwVnH00O6ZqyzwvClS"
                "kvV7vQemuNA0jZvcwqaxOaIUGhaWuKNS3H5nIE2d0IheE0uxLzEkWBpXfA8rwPdqYT1irUouFGgn"
                "jCYWioSOo1HWD/yGsBZQu5uYhEm2xhwCmOUJ7YWGQMIOgwL3xwkmIGUQ8m18XjRpaxkKb+Or+nMz"
                "u59lyx1MjPwQOZYJHVKSQ8GPEpemnsJlnkHb4BNHnsbW1MSGOdN4F4Lg7XlCh/2s0Pq88EaYzhBU"
                "zNA3EDDbXfjZPf6ayyP8LmDerHXst479OwqvEVnCCfRfmbPvvarNfLV5G2dRZzxfTMedKBr+1wS7"
                "WUF43hdu90I7IqHwqr3u44ASe17ZGaCpmu+xNYhGNWHpfxnYQPD3hTF4BeHF2n+b/gBQSwMEFAAA"
                "AAgAhjw2XWCrF+RxAQAAfgIAABgAAAB4bC93b3Jrc2hlZXRzL3NoZWV0Mi54bWx1UsFu2zAM/RVB"
                "91apsXZFYRtIOgwrsHVB0nW9KjYdC5FEj2Lm9e9HuamRw3YwzEeRj+9RKkekQ+oBWP0JPqZK98zD"
                "nTGp6SHYdIkDRDnpkIJlgbQ3aSCw7dQUvCkWixsTrIu6LqfcmuoSj+xdhDWpdAzB0usKPI6VvtLv"
                "iY3b95wTpi4Hu4ct8I9hTYLMzNK6ADE5jIqgq/Ty6m5Z5Pqp4NnBmM5ilZ3sEA8ZPLSVXmRB4KHh"
                "zGDl9xvuwftMJDJ+nTj1PDI3nsfv7J8n7+JlZxPco//pWu4rfatVC509et7g+AVOfq5ngZ8s27ok"
                "HBVln3XZ5CDPljoX8362TJJ3MojrR2QoDYuAjE0jn/TOBMVMUPyHYAsNxlax3SlZ8AFIvXzdvjwt"
                "V8XFarN8/n5RFB+UlyUkhdG/qh7onwPNmfp8M98s7V1MykMncxeXH6+1oje3b4BxmG52h8wYprCX"
                "BwKUC+S8Q7F2AnnZ85Or/wJQSwMEFAAAAAgAhjw2XXzzo9xRAgAA9gkAAA0AAAB4bC9zdHlsZXMu"
                "eG1s3VbbitswEP0V4Q+ok5g1cUnyUENgoS0Luw99VWI5EejiyvKS9Os7Izl2s6tZKH2rTfDMHJ25"
                "G2fT+6sSz2chPLtoZfptdva++5zn/fEsNO8/2U4YQFrrNPegulPed07wpkeSVvlqsShzzaXJdhsz"
                "6L32PTvawfhttsjy3aa1ZrYss2iAo1wL9srVNqu5kgcnw1mupbpG8woNR6usYx5SEUgGS/8rwsuo"
                "YZajHy2NdWjMY4Tw6MGpVGpKYJVFw27Tce+FM3tQAicY30FslF+uHWRwcvy6XD1kMyE8IMjBuka4"
                "uzqjabdRovVAcPJ0xqe3XY6g91aD0Eh+soaHHG6MUQC3R6HUM47oR3vn+9Ky2OvHBtvMsNSbCAmN"
                "YnQTFfT/p7fo+5/dsk6+Wv9lgGpM0H8O1osnJ1p5CfqlvY8/hQ6J3EWfrAyXY5t9x51Tswt2GKTy"
                "0ozaWTaNMO9qA/eeH2Cp7/zD+Ua0fFD+ZQK32Sx/E40cdDWdesKyxlOz/BVnuCynzYRY0jTiIpp6"
                "VN3pEEQGAkQdLyS8RfbhSiMUJ2JpBDEqDpUBxYksKs7/VM+arCdiVG7rJLImOWuSE1kppA43FSfN"
                "qeBKV1pVRVGWVEfrOplBTfWtLPGX9kblhgwqDkb6u17T06Y35OM9oGb60YZQldKbSFVK9xqRdN+Q"
                "UVXpaVNxkEFNgdodjJ+OgzuV5hQFTpXKjXqDaaSqKAR3Mb2jZUl0p8Q7PR/qLSmKqkojiKUzKAoK"
                "wbeRRqgMMAcKKYrwHXzzPcpv36l8/qe3+w1QSwMEFAAAAAgAhjw2XZeKuxzAAAAAEwIAAAsAAABf"
                "cmVscy8ucmVsc52SuW7DMAxAf8XQnjAH0CGIM2XxFgT5AVaiD9gSBYpFnb+v2qVxkAsZeT08Etwe"
                "aUDtOKS2i6kY/RBSaVrVuAFItiWPac6RQq7ULB41h9JARNtjQ7BaLD5ALhlmt71kFqdzpFeIXNed"
                "pT3bL09Bb4CvOkxxQmlISzMO8M3SfzL38ww1ReVKI5VbGnjT5f524EnRoSJYFppFydOiHaV/Hcf2"
                "kNPpr2MitHpb6PlxaFQKjtxjJYxxYrT+NYLJD+x+AFBLAwQUAAAACACGPDZdGscsA0gBAACxAgAA"
                "DwAAAHhsL3dvcmtib29rLnhtbLVS0WrDMAz8leAPWNKwFVaavqxsK4ytrKPvTqI0orYVZKVd+/Vz"
                "EsICg7GXPck6ifPd2csz8TEnOkaf1jifqVqkWcSxL2qw2t9QAy5MKmKrJbR8iH3DoEtfA4g1cZok"
                "89hqdGq1HLm2HE8bEigEyQWwA/YIZ/8979rohB5zNCiXTPVnAyqy6NDiFcpMJSryNZ2fifFKTrTZ"
                "FUzGZGo2DPbAgsUPeNeJ/NC57xHR+bsOQjI1TwJhheyl3+j5ddB4grA8dK3QIxoBXmuBJ6a2QXfo"
                "aIKLeGKjz2GsQ4gL/kuMVFVYwJqK1oKTIUcG0wl0vsbGq8hpC5natdZqvnSOwhWbcnAnQdYkK15g"
                "GPCm7AX+n5g1iEbjJ2LSX8SkfVpjRCVU6KB8DUQ+4OG5ii1HXelNpbd3s/vwLK0xDwF7cy+kyzHx"
                "8besvgBQSwMEFAAAAAgAhjw2XY33LFq0AAAAiQIAABoAAAB4bC9fcmVscy93b3JrYm9vay54bWwu"
                "cmVsc8WSTQqDMBBGrxJygI7a0kVRV924LV4g6PiD0YTMlOrta3WhgS66ka7CNyHvezCJH6gVt2ag"
                "prUkxl4PlMiG2d4AqGiwV3QyFof5pjKuVzxHV4NVRadqhCgIruD2DJnGe6bIJ4u/EE1VtQXeTfHs"
                "ceAvYHgZ11GDyFLkytXIiYRRb2OC5QhPM1mKrEyky8pQwr+FIk8oOlCIeNJIm82avfrzgfU8v8Wt"
                "fYnr0N/J5eMA3s9L31BLAwQUAAAACACGPDZdbqckvB4BAABXBAAAEwAAAFtDb250ZW50X1R5cGVz"
                "XS54bWzFlM9OwzAMxl+lynVqMnbggNZdgCvswAuE1l2j5p9ib3Rvj9tuk0CjYioSl0aN7e/n+Iuy"
                "fjtGwKxz1mMhGqL4oBSWDTiNMkTwHKlDcpr4N+1U1GWrd6BWy+W9KoMn8JRTryE26yeo9d5S9tzx"
                "NprgC5HAosgex8SeVQgdozWlJo6rg6++UfITQXLlkIONibjgBKGuEvrIz4BT3esBUjIVZFud6EU7"
                "zlKdVUhHCyinJa70GOralFCFcu+4RGJMoCtsAMhZOYoupsnEE4bxezebP8hMATlzm0JEdizB7biz"
                "JX11HlkIEpnpI16ILD37fNC7XUH1SzaP9yOkdvAD1bDMn/FXjy/6N/ax+sc+3kNo//qq96t02vgz"
                "Xw3vyeYTUEsBAhQDFAAAAAgAhjw2XUbHTUiVAAAAzQAAABAAAAAAAAAAAAAAAIABAAAAAGRvY1By"
                "b3BzL2FwcC54bWxQSwECFAMUAAAACACGPDZdfwa/WO4AAAArAgAAEQAAAAAAAAAAAAAAgAHDAAAA"
                "ZG9jUHJvcHMvY29yZS54bWxQSwECFAMUAAAACACGPDZdmVycIxAGAACcJwAAEwAAAAAAAAAAAAAA"
                "gAHgAQAAeGwvdGhlbWUvdGhlbWUxLnhtbFBLAQIUAxQAAAAIAIY8Nl1O4DiubgEAAMMCAAAYAAAA"
                "AAAAAAAAAACAgSEIAAB4bC93b3Jrc2hlZXRzL3NoZWV0MS54bWxQSwECFAMUAAAACACGPDZdYKsX"
                "5HEBAAB+AgAAGAAAAAAAAAAAAAAAgIHFCQAAeGwvd29ya3NoZWV0cy9zaGVldDIueG1sUEsBAhQD"
                "FAAAAAgAhjw2XXzzo9xRAgAA9gkAAA0AAAAAAAAAAAAAAIABbAsAAHhsL3N0eWxlcy54bWxQSwEC"
                "FAMUAAAACACGPDZdl4q7HMAAAAATAgAACwAAAAAAAAAAAAAAgAHoDQAAX3JlbHMvLnJlbHNQSwEC"
                "FAMUAAAACACGPDZdGscsA0gBAACxAgAADwAAAAAAAAAAAAAAgAHRDgAAeGwvd29ya2Jvb2sueG1s"
                "UEsBAhQDFAAAAAgAhjw2XY33LFq0AAAAiQIAABoAAAAAAAAAAAAAAIABRhAAAHhsL19yZWxzL3dv"
                "cmtib29rLnhtbC5yZWxzUEsBAhQDFAAAAAgAhjw2XW6nJLweAQAAVwQAABMAAAAAAAAAAAAAAIAB"
                "MhEAAFtDb250ZW50X1R5cGVzXS54bWxQSwUGAAAAAAoACgCEAgAAgRIAAAAA"
            ),
        }
    ],
    "folder-hr": [
        {
            "id": "file-leave-policy",
            "name": "Leave Policy.txt",
            "mimeType": "text/plain",
            "webViewLink": "https://drive.google.com/file/d/file-leave-policy/view",
            "modifiedTime": "2024-08-15T08:00:00Z",
            "permissions": [{"type": "domain", "role": "reader"}],
            "content": (
                "HR Leave Policy.\n"
                "Employees receive 18 days of paid leave per calendar year.\n"
                "Leave requests require manager approval within 3 business days.\n"
            ),
        },
        {
            # Phase I smoke fixture: an old-format Word file for worker/
            # pipeline/process.py's `catdoc` path (see _parse_legacy_office).
            # These raw bytes are not a real OLE2 .doc -- constructing a
            # genuinely valid legacy .doc requires an OLE2 compound-file
            # writer that isn't available in this image (no LibreOffice/
            # antiword/olefile). catdoc falls back to reading non-OLE input
            # as plain text and exits 0, so the marker word below survives
            # and is searchable -- proving the pipeline calls catdoc and
            # indexes its (real) stdout, which is what this scenario needs.
            "id": "file-legacy-memo",
            "name": "Legacy Memo.doc",
            "mimeType": "application/msword",
            "webViewLink": "https://drive.google.com/file/d/file-legacy-memo/view",
            "modifiedTime": "2025-04-01T09:00:00Z",
            "permissions": [{"type": "domain", "role": "reader"}],
            "content_b64": (
                "TGVnYWN5IG9mZmljZSBtZW1vIHBsYWNlaG9sZGVyLiBNYXJrZXIgd29yZCBDQVRET0NNQVJLRVIt"
                "NzczMSBhcHBlYXJzIG9uY2UgZm9yIHNlYXJjaC4="
            ),
        },
    ],
    "folder-finance": [
        {
            "id": "file-gst-sop",
            "name": "GST Invoice SOP.txt",
            "mimeType": "text/plain",
            "webViewLink": "https://drive.google.com/file/d/file-gst-sop/view",
            "modifiedTime": "2025-01-10T12:00:00Z",
            "permissions": [
                {"type": "user", "emailAddress": "owner@example.com", "role": "owner"},
                {"type": "user", "emailAddress": "finance@example.com", "role": "reader"},
            ],
            "content": (
                "Finance SOP.\n"
                "GST invoice must be issued within 7 days of payment receipt.\n"
                "Professional plan is priced at INR 24,999 per month billed annually.\n"
            ),
        },
        {
            # Phase I (Drive completeness) smoke fixture: a real in-memory ZIP
            # (built with Python's zipfile, base64-encoded) containing two
            # inner text files -- Reports/Leave.txt and Contracts/NDA.txt.
            # Exercises worker/drive_sync.py's _upsert_drive_zip: each inner
            # file becomes its own Document, cited by "<zip name> / <inner
            # path>". See scripts/smoke-phase-i.sh. Its content can be
            # swapped mid-run via the "content_b64" mock override (below) to
            # simulate the zip shrinking to fewer inner files.
            "id": "file-mock-zip",
            "name": "Reports Bundle.zip",
            "mimeType": "application/zip",
            "webViewLink": "https://drive.google.com/file/d/file-mock-zip/view",
            "modifiedTime": "2025-04-01T09:00:00Z",
            "permissions": [{"type": "domain", "role": "reader"}],
            "content_b64": (
                "UEsDBBQAAAAAANY7Nl1kPmpyXAAAAFwAAAARAAAAUmVwb3J0cy9MZWF2ZS50eHRaSVAgaW5uZXIg"
                "TGVhdmUgZG9jLgpFbXBsb3llZXMgZ2V0IDIxIGRheXMgYW5udWFsIGxlYXZlIHVuZGVyIHRoZSBa"
                "SVBURVNULUxFQVZFLTlGMyBwb2xpY3kuClBLAwQUAAAAAADWOzZdHn/fzk4AAABOAAAAEQAAAENv"
                "bnRyYWN0cy9OREEudHh0WklQIGlubmVyIE5EQSBkb2MuCk11dHVhbCBjb25maWRlbnRpYWxpdHkg"
                "YXBwbGllcyBwZXIgY2xhdXNlIFpJUFRFU1QtTkRBLTdLMi4KUEsBAhQDFAAAAAAA1js2XWQ+anJc"
                "AAAAXAAAABEAAAAAAAAAAAAAAIABAAAAAFJlcG9ydHMvTGVhdmUudHh0UEsBAhQDFAAAAAAA1js2"
                "XR5/385OAAAATgAAABEAAAAAAAAAAAAAAIABiwAAAENvbnRyYWN0cy9OREEudHh0UEsFBgAAAAAC"
                "AAIAfgAAAAgBAAAAAA=="
            ),
        },
        {
            "id": "file-finance-group-shared",
            "name": "Finance Group Shared Report.txt",
            "mimeType": "text/plain",
            "webViewLink": "https://drive.google.com/file/d/file-finance-group-shared/view",
            "modifiedTime": "2025-02-01T09:00:00Z",
            # Shared with the "finance@acme.com" Google Group (see
            # services/groups.py MOCK_GROUPS) rather than an individual user or
            # the whole domain. This is the fixture the Phase G smoke test
            # (scripts/smoke-phase-g.sh) uses to prove group-based access: a
            # group member should see this file once Groups sync resolves the
            # membership, and lose access again once this permission is
            # revoked and Drive re-syncs -- even though the file content and
            # modifiedTime never change. See get_mock_files() below for how
            # the smoke test mutates this between two sync calls.
            "permissions": [
                {"type": "user", "emailAddress": "owner@example.com", "role": "owner"},
                {"type": "group", "emailAddress": "finance@acme.com", "role": "reader"},
            ],
            "content": (
                "Finance Group Shared Report.\n"
                "Visible to the Finance Google Group only.\n"
            ),
        },
    ],
    "folder-shared-legal": [
        {
            "id": "file-nda-template",
            "name": "NDA Template.txt",
            "mimeType": "text/plain",
            "webViewLink": "https://drive.google.com/file/d/file-nda-template/view",
            "modifiedTime": "2025-03-01T09:00:00Z",
            "permissions": [{"type": "domain", "role": "reader"}],
            "content": "NDA Template.\nStandard mutual non-disclosure agreement.\n",
        }
    ],
}


# --- Test-only mock permission overrides ------------------------------------
#
# The Phase G smoke test needs to mutate a mock file's Drive `permissions`
# between two sync calls in the same run (e.g. revoke a group share, then
# re-sync) to prove worker/drive_sync.py's fail-closed re-resolution path
# actually revokes access. The api and worker run as separate processes
# (separate containers in docker-compose), so an in-memory mutation from one
# wouldn't be visible to the other -- but docker-compose bind-mounts this same
# `apps/api` source tree into both, so a small JSON file living next to this
# module is a cheap, host-writable side channel between an external test
# script and both processes, without a real API endpoint or IPC mechanism.
#
# This is consulted ONLY by get_mock_files(), which is itself only ever
# called from the mock-mode fixture branch in
# worker/drive_sync.py::_list_files_for_folders (already gated on
# `settings.google_drive_mode == "mock"`). It has no effect on, and is never
# read from, the real Google Drive API code path. If the file is absent (the
# default for anyone not running this smoke test), MOCK_FILES is served
# as-is.
_MOCK_OVERRIDES_PATH = Path(
    os.environ.get("DRIVE_MOCK_OVERRIDES_PATH")
    or (Path(__file__).resolve().parents[2] / ".mock-drive-overrides.json")
)


def get_mock_files(folder_id: str) -> list[dict[str, Any]]:
    """Mock-mode file listing for `folder_id`, with any test-only overrides from
    `_MOCK_OVERRIDES_PATH` applied on top of MOCK_FILES:

    * ``permissions``  - {file_id: [permission, ...]} replaces a file's permissions
    * ``removed``      - [file_id, ...] omits those files (simulates delete/trash)
    * ``fail_listing`` - true makes the listing raise (simulates a Google outage)
    * ``content_b64``  - {file_id: base64 string} replaces a file's content_b64,
      e.g. simulating a ZIP shrinking to fewer inner entries between two syncs
      (see scripts/smoke-phase-i.sh scenario 2's "lighter case") without
      needing a real Google file to actually change.
    """
    overrides = _read_mock_overrides()
    if overrides.get("fail_listing"):
        raise RuntimeError("mock Drive listing failure (fail_listing override)")
    removed = set(overrides.get("removed") or [])
    permission_overrides = overrides.get("permissions") or {}
    content_overrides = overrides.get("content_b64") or {}
    files = [dict(f) for f in MOCK_FILES.get(folder_id) or [] if f["id"] not in removed]
    for f in files:
        if f["id"] in permission_overrides:
            f["permissions"] = permission_overrides[f["id"]]
        if f["id"] in content_overrides:
            f["content_b64"] = content_overrides[f["id"]]
    return files


def _read_mock_overrides() -> dict[str, Any]:
    try:
        return json.loads(_MOCK_OVERRIDES_PATH.read_text())
    except (FileNotFoundError, ValueError, OSError):
        return {}


@dataclass
class DriveFolder:
    id: str
    name: str
    path: str
    drive_name: str | None = None


class DriveService:
    def __init__(
        self,
        db: Session,
        settings: Settings,
        tokens: TokenStore,
        storage: ObjectStorage,
        queue: IngestQueue,
    ):
        self.db = db
        self.settings = settings
        self.tokens = tokens
        self.storage = storage
        self.queue = queue

    def require_ready(self) -> None:
        if not self.settings.google_drive_ready:
            raise AppError(
                "CONNECTOR_NOT_AVAILABLE",
                "Google Drive isn't available yet.",
                501,
            )

    def get_connection(self, tenant_id: UUID) -> Optional[Connection]:
        return self.db.scalar(
            select(Connection)
            .where(
                Connection.tenant_id == tenant_id,
                Connection.connector_type == "google_drive",
            )
            .options(selectinload(Connection.credentials))
        )

    def connection_detail(self, tenant_id: UUID) -> dict[str, Any]:
        conn = self.get_connection(tenant_id)
        if not conn:
            return {
                "connected": False,
                "status": "available" if self.settings.google_drive_ready else "not_implemented",
                "health": None,
                "account_email": None,
                "last_sync_at": None,
                "last_error": None,
                "document_count": 0,
                "failed_document_count": 0,
                "selected_folder_ids": [],
                "mode": self.settings.google_drive_mode,
                "auto_sync_enabled": None,
                "auto_sync_paused_reason": None,
            }
        docs = int(
            self.db.scalar(
                select(func.count())
                .select_from(Document)
                .where(
                    Document.tenant_id == tenant_id,
                    Document.connection_id == conn.id,
                    Document.deleted_at.is_(None),
                )
            )
            or 0
        )
        failed = int(
            self.db.scalar(
                select(func.count())
                .select_from(Document)
                .where(
                    Document.tenant_id == tenant_id,
                    Document.connection_id == conn.id,
                    Document.status == DocumentStatus.failed,
                    Document.deleted_at.is_(None),
                )
            )
            or 0
        )
        return {
            "connected": conn.status
            in {ConnectionStatus.connected, ConnectionStatus.syncing, ConnectionStatus.sync_failed},
            "status": conn.status.value,
            "health": conn.health.value if conn.health else "unknown",
            "account_email": conn.account_email,
            "last_sync_at": conn.last_sync_at,
            "last_error": conn.last_error,
            "document_count": docs,
            "failed_document_count": failed,
            "selected_folder_ids": list((conn.config or {}).get("selected_folder_ids") or []),
            "mode": self.settings.google_drive_mode,
            "connection_id": conn.id,
            "auto_sync_enabled": autosync.is_enabled(conn.config),
            "auto_sync_paused_reason": autosync.paused_reason(conn.config),
        }

    def set_auto_sync(self, *, tenant_id: UUID, user_id: UUID, enabled: bool) -> None:
        conn = self.get_connection(tenant_id)
        if not conn or conn.status == ConnectionStatus.disconnected:
            raise AppError("DRIVE_NOT_CONNECTED", "Connect Google Drive first.", 400)
        autosync.set_auto_sync(self.db, conn, enabled=enabled, actor_user_id=user_id)

    # --- OAuth ---

    def oauth_start_url(self, *, tenant_id: UUID, user_id: UUID, state: str) -> str:
        self.require_ready()
        if self.settings.google_drive_mode.lower() == "mock":
            return f"{self.settings.app_url.rstrip('/')}/v1/connections/google_drive/oauth/callback?code=mock&state={state}"
        params = {
            "client_id": self.settings.google_client_id,
            "redirect_uri": self.settings.google_drive_redirect_uri,
            "response_type": "code",
            "scope": " ".join(self.settings.drive_scope_list),
            "access_type": "offline",
            "include_granted_scopes": "true",
            "prompt": "consent",
            "state": state,
        }
        return "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(params)

    def complete_oauth(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        code: str,
    ) -> Connection:
        self.require_ready()
        if self.settings.google_drive_mode.lower() == "mock" or code == "mock":
            return self._upsert_connection(
                tenant_id=tenant_id,
                user_id=user_id,
                account_email="mock-drive@vridhi.local",
                refresh_token="mock-refresh-token",
                access_token="mock-access-token",
                expires_in=3600,
                scopes=" ".join(self.settings.drive_scope_list),
            )

        token_resp = httpx.post(
            "https://oauth2.googleapis.com/token",
            data={
                "code": code,
                "client_id": self.settings.google_client_id,
                "client_secret": self.settings.google_client_secret,
                "redirect_uri": self.settings.google_drive_redirect_uri,
                "grant_type": "authorization_code",
            },
            timeout=30.0,
        )
        token_resp.raise_for_status()
        token_data = token_resp.json()
        access_token = token_data["access_token"]
        refresh_token = token_data.get("refresh_token")
        if not refresh_token:
            raise AppError(
                "DRIVE_TOKEN_MISSING",
                "Google did not return a refresh token. Disconnect the app in Google Account and reconnect.",
                400,
            )
        email = self._fetch_user_email(access_token)
        return self._upsert_connection(
            tenant_id=tenant_id,
            user_id=user_id,
            account_email=email,
            refresh_token=refresh_token,
            access_token=access_token,
            expires_in=int(token_data.get("expires_in") or 3600),
            scopes=token_data.get("scope") or " ".join(self.settings.drive_scope_list),
        )

    def disconnect(self, *, tenant_id: UUID, user_id: UUID) -> None:
        conn = self.get_connection(tenant_id)
        if not conn:
            return
        creds = conn.credentials
        if creds:
            self.db.delete(creds)
        conn.status = ConnectionStatus.disconnected
        conn.health = ConnectionHealth.unknown
        conn.account_email = None
        conn.config = {**(conn.config or {}), "selected_folder_ids": [], "page_token": None}
        conn.updated_at = utcnow()
        self.db.commit()

    def _upsert_connection(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        account_email: str,
        refresh_token: str,
        access_token: str,
        expires_in: int,
        scopes: str,
    ) -> Connection:
        conn = self.get_connection(tenant_id)
        if not conn:
            conn = Connection(
                id=uuid4(),
                tenant_id=tenant_id,
                connector_type="google_drive",
                status=ConnectionStatus.connected,
                health=ConnectionHealth.healthy,
                config={},
            )
            self.db.add(conn)
            self.db.flush()
        conn.status = ConnectionStatus.connected
        conn.health = ConnectionHealth.healthy
        conn.connected_by_user_id = user_id
        conn.account_email = account_email
        conn.last_error = None
        conn.last_error_at = None
        conn.updated_at = utcnow()

        creds = conn.credentials or ConnectionCredential(
            id=uuid4(),
            tenant_id=tenant_id,
            connection_id=conn.id,
        )
        creds.encrypted_refresh_token = self.tokens.encrypt(refresh_token)
        creds.encrypted_access_token = self.tokens.encrypt(access_token)
        creds.access_token_expires_at = utcnow() + timedelta(seconds=max(expires_in - 60, 60))
        creds.scopes = scopes
        creds.token_backend = self.settings.token_backend
        if not conn.credentials:
            self.db.add(creds)
        self.db.commit()
        self.db.refresh(conn)
        return conn

    def _fetch_user_email(self, access_token: str) -> str:
        resp = httpx.get(
            "https://www.googleapis.com/oauth2/v2/userinfo",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=20.0,
        )
        resp.raise_for_status()
        return str(resp.json().get("email") or "unknown@google")

    # --- Folders ---

    def list_folders(self, *, tenant_id: UUID) -> list[DriveFolder]:
        self.require_ready()
        conn = self.get_connection(tenant_id)
        if not conn or conn.status == ConnectionStatus.disconnected:
            raise AppError("DRIVE_NOT_CONNECTED", "Connect Google Drive first.", 400)
        if self.settings.google_drive_mode.lower() == "mock":
            return [DriveFolder(**f) for f in MOCK_FOLDERS]
        access = self._access_token(conn)
        folders: list[DriveFolder] = []
        page_token = None
        while True:
            params: dict[str, Any] = {
                "q": "mimeType='application/vnd.google-apps.folder' and trashed=false",
                "spaces": "drive",
                "fields": "nextPageToken, files(id, name, parents)",
                "pageSize": self.settings.drive_sync_page_size,
            }
            if page_token:
                params["pageToken"] = page_token
            resp = httpx.get(
                "https://www.googleapis.com/drive/v3/files",
                params=params,
                headers={"Authorization": f"Bearer {access}"},
                timeout=30.0,
            )
            resp.raise_for_status()
            data = resp.json()
            for f in data.get("files") or []:
                folders.append(
                    DriveFolder(id=f["id"], name=f.get("name") or "Untitled", path=f.get("name") or "Untitled")
                )
            page_token = data.get("nextPageToken")
            if not page_token:
                break

        # Shared Drives: list the drives themselves, then each drive's folders.
        drives_resp = httpx.get(
            "https://www.googleapis.com/drive/v3/drives",
            params={"pageSize": self.settings.drive_sync_page_size},
            headers={"Authorization": f"Bearer {access}"},
            timeout=30.0,
        )
        drives_resp.raise_for_status()
        for shared_drive in drives_resp.json().get("drives") or []:
            drive_id = shared_drive["id"]
            drive_name = shared_drive.get("name") or "Shared Drive"
            page_token = None
            while True:
                params = {
                    "q": "mimeType='application/vnd.google-apps.folder' and trashed=false",
                    "corpora": "drive",
                    "driveId": drive_id,
                    "includeItemsFromAllDrives": "true",
                    "supportsAllDrives": "true",
                    "fields": "nextPageToken, files(id, name, parents)",
                    "pageSize": self.settings.drive_sync_page_size,
                }
                if page_token:
                    params["pageToken"] = page_token
                resp = httpx.get(
                    "https://www.googleapis.com/drive/v3/files",
                    params=params,
                    headers={"Authorization": f"Bearer {access}"},
                    timeout=30.0,
                )
                resp.raise_for_status()
                data = resp.json()
                for f in data.get("files") or []:
                    folders.append(
                        DriveFolder(
                            id=f["id"],
                            name=f.get("name") or "Untitled",
                            path=f"{drive_name} / {f.get('name') or 'Untitled'}",
                            drive_name=drive_name,
                        )
                    )
                page_token = data.get("nextPageToken")
                if not page_token:
                    break
            # The drive itself is also a selectable "folder" (its id doubles
            # as its root folder id).
            folders.append(
                DriveFolder(id=drive_id, name=drive_name, path=drive_name, drive_name=drive_name)
            )
        return folders

    def save_selected_folders(self, *, tenant_id: UUID, folder_ids: list[str]) -> Connection:
        conn = self.get_connection(tenant_id)
        if not conn:
            raise AppError("DRIVE_NOT_CONNECTED", "Connect Google Drive first.", 400)
        known = {f.id for f in self.list_folders(tenant_id=tenant_id)}
        invalid = [fid for fid in folder_ids if fid not in known]
        if invalid and self.settings.google_drive_mode.lower() != "mock":
            # Real Drive: allow any folder id returned by API; if empty known list, accept selected
            pass
        if self.settings.google_drive_mode.lower() == "mock":
            invalid = [fid for fid in folder_ids if fid not in {f["id"] for f in MOCK_FOLDERS}]
            if invalid:
                raise AppError("INVALID_FOLDER", f"Unknown folder ids: {', '.join(invalid)}", 400)
        cfg = dict(conn.config or {})
        cfg["selected_folder_ids"] = list(dict.fromkeys(folder_ids))
        conn.config = cfg
        conn.updated_at = utcnow()
        self.db.commit()
        self.db.refresh(conn)
        return conn

    # --- Sync ---

    def start_sync(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        folder_ids: Optional[list[str]] = None,
        visibility: DocumentVisibility = DocumentVisibility.org,
        selected_user_ids: Optional[list[UUID]] = None,
        incremental: bool = True,
        trigger: str = "manual",
    ) -> SyncJob:
        self.require_ready()
        conn = self.get_connection(tenant_id)
        if not conn or conn.status == ConnectionStatus.disconnected:
            raise AppError("DRIVE_NOT_CONNECTED", "Connect Google Drive first.", 400)

        selected = folder_ids or list((conn.config or {}).get("selected_folder_ids") or [])
        if not selected:
            raise AppError("FOLDERS_REQUIRED", "Select at least one Drive folder to sync.", 400)

        if visibility == DocumentVisibility.selected and not selected_user_ids:
            raise AppError(
                "SELECTED_USERS_REQUIRED",
                "Select at least one user for selected visibility.",
                400,
            )

        cfg = dict(conn.config or {})
        cfg["selected_folder_ids"] = selected
        cfg["default_visibility"] = visibility.value
        if selected_user_ids:
            cfg["selected_user_ids"] = [str(u) for u in selected_user_ids]
        conn.config = cfg
        conn.status = ConnectionStatus.syncing
        conn.health = ConnectionHealth.healthy
        conn.last_error = None

        job = SyncJob(
            id=uuid4(),
            tenant_id=tenant_id,
            connection_id=conn.id,
            job_type=SyncJobType.drive_sync,
            status=SyncJobStatus.queued,
            max_attempts=self.settings.ingest_max_attempts,
            payload={
                "folder_ids": selected,
                "visibility": visibility.value,
                "selected_user_ids": [str(u) for u in (selected_user_ids or [])],
                "incremental": incremental,
                "trigger": trigger,
                "requested_by": str(user_id),
                "page_token": (conn.config or {}).get("page_token") if incremental else None,
            },
        )
        self.db.add(job)
        # Commit (making the job row durably visible to other DB sessions)
        # BEFORE publishing to the queue. Publishing first and committing
        # after is a classic race: the worker runs in a separate process
        # with its own DB session/connection, and a fast worker can receive
        # and look up the job before this transaction lands, find nothing,
        # and silently ack the message -- leaving the job stuck at "queued"
        # forever with no error anywhere. This was observed directly against
        # the mock stack (worker consumed and deleted the SQS message inside
        # single-digit milliseconds of it being sent, well before this
        # session's commit had a chance to complete).
        self.db.commit()
        self.db.refresh(job)
        try:
            message_id = self.queue.enqueue_job(
                job_id=job.id,
                tenant_id=tenant_id,
                job_type="drive_sync",
                document_id=None,
                version_id=None,
            )
            job.sqs_message_id = message_id
            self.db.commit()
        except Exception as exc:
            job.status = SyncJobStatus.failed
            job.error_message = str(exc)
            conn.status = ConnectionStatus.sync_failed
            conn.health = ConnectionHealth.error
            conn.last_error = str(exc)
            conn.last_error_at = utcnow()
            self.db.commit()
            raise AppError("QUEUE_ERROR", "Could not queue Drive sync.", 503) from exc

        self.db.refresh(job)
        return job

    def list_sync_history(self, *, tenant_id: UUID, limit: int = 20) -> list[SyncJob]:
        conn = self.get_connection(tenant_id)
        if not conn:
            return []
        return list(
            self.db.scalars(
                select(SyncJob)
                .where(
                    SyncJob.tenant_id == tenant_id,
                    SyncJob.connection_id == conn.id,
                    SyncJob.job_type == SyncJobType.drive_sync,
                )
                .order_by(SyncJob.created_at.desc())
                .limit(limit)
            ).all()
        )

    def list_failed_documents(self, *, tenant_id: UUID, limit: int = 50) -> list[Document]:
        conn = self.get_connection(tenant_id)
        if not conn:
            return []
        return list(
            self.db.scalars(
                select(Document)
                .where(
                    Document.tenant_id == tenant_id,
                    Document.connection_id == conn.id,
                    Document.status == DocumentStatus.failed,
                    Document.deleted_at.is_(None),
                )
                .order_by(Document.updated_at.desc())
                .limit(limit)
            ).all()
        )

    def get_sync_job(self, *, tenant_id: UUID, job_id: UUID) -> SyncJob:
        job = self.db.scalar(
            select(SyncJob).where(SyncJob.id == job_id, SyncJob.tenant_id == tenant_id)
        )
        if not job:
            raise AppError("JOB_NOT_FOUND", "Sync job not found.", 404)
        return job

    # --- Worker-facing helpers ---

    def _access_token(self, conn: Connection) -> str:
        creds = conn.credentials
        if not creds or not creds.encrypted_access_token:
            raise RuntimeError("Missing Drive credentials")
        if (
            creds.access_token_expires_at
            and creds.access_token_expires_at > utcnow() + timedelta(seconds=30)
        ):
            return self.tokens.decrypt(creds.encrypted_access_token)
        if self.settings.google_drive_mode.lower() == "mock":
            return "mock-access-token"
        if not creds.encrypted_refresh_token:
            raise RuntimeError("Missing refresh token")
        refresh = self.tokens.decrypt(creds.encrypted_refresh_token)
        resp = httpx.post(
            "https://oauth2.googleapis.com/token",
            data={
                "client_id": self.settings.google_client_id,
                "client_secret": self.settings.google_client_secret,
                "refresh_token": refresh,
                "grant_type": "refresh_token",
            },
            timeout=30.0,
        )
        resp.raise_for_status()
        data = resp.json()
        access = data["access_token"]
        creds.encrypted_access_token = self.tokens.encrypt(access)
        creds.access_token_expires_at = utcnow() + timedelta(seconds=int(data.get("expires_in") or 3600) - 60)
        self.db.commit()
        return access

    def map_permissions_to_acl(
        self,
        *,
        tenant_id: UUID,
        permissions: list[dict[str, Any]],
        default_visibility: DocumentVisibility,
        selected_user_ids: list[UUID],
    ) -> tuple[DocumentVisibility, list[UUID], list[UUID]]:
        """
        Conservative fail-closed mapping:
        - domain/anyone/org-wide Drive share -> org (only if default allows org)
        - user shares -> selected grants for matched active members only
        - group shares -> selected grants for the matched local Group (if known)
        - unmatched emails/groups are skipped (never widen access)
        - if no mappable grants and not domain -> private

        Returns (visibility, user_grant_ids, group_grant_ids).
        """
        if default_visibility == DocumentVisibility.private:
            return DocumentVisibility.private, [], []
        if default_visibility == DocumentVisibility.selected:
            return DocumentVisibility.selected, selected_user_ids, []

        # default org -- still fail-closed if Drive file is only shared to specific users/groups
        has_domain = any(
            p.get("type") in {"domain", "anyone"} and p.get("role") in {"reader", "commenter", "writer", "owner"}
            for p in permissions
        )
        if has_domain:
            return DocumentVisibility.org, [], []

        emails = [
            str(p.get("emailAddress") or "").lower()
            for p in permissions
            if p.get("type") == "user" and p.get("emailAddress")
        ]
        group_emails = [
            str(p.get("emailAddress") or "").lower()
            for p in permissions
            if p.get("type") == "group" and p.get("emailAddress")
        ]

        user_ids = list(
            self.db.execute(
                select(User.id)
                .join(OrganizationMember, OrganizationMember.user_id == User.id)
                .where(
                    OrganizationMember.tenant_id == tenant_id,
                    OrganizationMember.status == MemberStatus.active,
                    func.lower(User.email).in_(emails),
                )
            )
            .scalars()
            .all()
        ) if emails else []

        group_ids = list(
            self.db.execute(
                select(Group.id).where(
                    Group.tenant_id == tenant_id,
                    func.lower(Group.email).in_(group_emails),
                )
            )
            .scalars()
            .all()
        ) if group_emails else []

        if not user_ids and not group_ids:
            # No discoverable/resolvable sharing metadata -> keep private
            return DocumentVisibility.private, [], []
        return DocumentVisibility.selected, user_ids, group_ids


def new_oauth_state() -> str:
    return generate_token(24)
