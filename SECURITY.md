# Security Policy

## Supported versions

Only the latest release on the `main` branch receives security fixes.
Pageplain is pre-1.0 (`1.0.0-rc.x`); there is no long-term-support branch yet.

## Reporting a vulnerability

**Please do not open a public GitHub issue for a security problem.**

Instead, use GitHub's private vulnerability reporting for this repository
(the "Report a vulnerability" button under the Security tab), or email the
maintainer listed in the repository's contact information. Include:

- A description of the issue and its impact (what an attacker could
  achieve — e.g. read a local file, reach an internal service, run script
  in the admin UI).
- Steps to reproduce, or a minimal proof-of-concept URL/request.
- The version or commit you tested against.

You should get an acknowledgement within a few days. Please give a
reasonable amount of time to ship a fix before any public disclosure.

## Scope

Pageplain fetches arbitrary URLs on your behalf, including URLs discovered
on pages it has already fetched, and it is commonly wired up as a tool an
AI agent calls directly. That combination makes a few bug classes
especially high-priority here, in roughly descending order of severity:

1. **SSRF / local file access** — anything that lets a request reach an
   address other than a public, routable host (`file://`, loopback,
   link-local, RFC 1918 ranges, cloud metadata endpoints), including via
   redirects, DNS rebinding, or the browser path.
2. **Authentication / authorization bypass** — anything that lets a caller
   without a valid API key reach `/v1/*`, or that lets one team's job data
   leak to another.
3. **Injection into the admin UI** — stored or reflected XSS via job names,
   URLs, or any other field a caller controls.
4. **Arbitrary command or code execution** — via crawled HTML/JS, job
   names, file paths, or configuration values.

Out of scope: the fact that Pageplain can fetch a page that contains
misleading or adversarial *text* (a prompt-injection attempt aimed at
whatever agent reads the markdown back). Pageplain's job is to make the
*network access* to that page safe; what a calling agent does with the
text it gets back is the agent's and its operator's responsibility. If
you find a way to turn scraped content into unsafe behavior *inside
Pageplain itself* (e.g. crawled content reaching a code path that
executes it), that is in scope under #4 above.
