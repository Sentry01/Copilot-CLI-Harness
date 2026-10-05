# Task Tracker — Product Requirements

## Summary
A small team task tracker. People sign up, create projects, add tasks with a status and due
date, and share a project with teammates. Teams of up to 20 people should find it fast and
pleasant to use on desktop and mobile.

## Users
- **Member**: creates projects and tasks, edits their own tasks, comments.
- **Project owner**: everything a member can do, plus inviting and removing members of their projects.

## Features
1. **Accounts**: sign up with email and password (minimum 10 characters), log in, log out.
   Sessions last 7 days. Duplicate emails are rejected with a clear message.
2. **Projects**: create, rename and archive projects. A project lists its tasks grouped by
   status (To do, In progress, Done). Archived projects are read-only.
3. **Tasks**: title (required, max 120 chars), optional description (markdown, max 5,000
   chars), status, optional due date, optional assignee (a project member). Tasks can be
   edited, moved between statuses, and deleted (with confirmation).
4. **Sharing**: owners invite members by email. Invited users see the project after they log
   in. Members cannot see projects they don't belong to.
5. **Search & filter**: filter tasks by status, assignee and overdue. Search task titles
   within a project.
6. **Comments**: members comment on tasks. Comments show author and relative time.

## Non-functional
- **Security**: passwords hashed, sessions in HttpOnly cookies, no access to other teams' data,
  login rate-limited, markdown rendered safely (no script execution).
- **Performance**: project page usable within 2 seconds on a mid-range laptop. Task list API
  responds in under 300 ms (p95) with 500 tasks.
- **Usability**: works at 375px width, fully keyboard operable, WCAG 2.1 AA, clear inline
  validation errors, helpful empty states ("No tasks yet — add your first task").

## Out of scope
Email delivery (invites are accepted by logging in with the invited email), file
attachments, real-time collaboration, native apps.

## Tech preferences
Any mainstream web stack that runs on Node 22 with SQLite. No external services.
