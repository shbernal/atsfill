---
name: freehire-search
description: Use when searching for jobs on freehire.me with the `freehire` CLI, filtering by keyword, location, seniority, skills, salary or remote work, opening a posting, or listing a company's open roles.
---

# Searching jobs with freehire

`freehire` is a CLI over the [freehire.me](https://freehire.me) job catalogue. `search`, `facets` and `company` work without an account. Always pass `--json` and pull the fields you need with `jq`. The plain output is for people and drops fields.

If the command is missing, install it:

```bash
mise use -g github:strelov1/freehire-cli
```

## 1. Learn the filter values

Never guess a filter value. Read the live vocabulary first:

```bash
freehire --json facets | jq '.facets | keys'
freehire --json facets | jq '.facets.seniority'
freehire --json facets --category backend | jq '.facets.skills | to_entries | sort_by(-.value) | .[:20]'
```

Each facet lists its values with a vacancy count. Filters narrow the counts to that slice. Skills are lowercase slugs (`go`, `react`, `kubernetes`).

## 2. Search

```bash
freehire --json search "golang" --region eu --remote --seniority senior --limit 10 | jq '.[] | {title, company, location, public_slug}'
freehire --json search "" --country IT --employment-type contract --exclude-skill python | jq '.[] | {title, company, public_slug}'
```

- Repeat a filter flag to OR its values.
- `--facet key=value` reaches any facet without its own flag. Add `_exclude` to the key to filter values out (`--facet company_type_exclude=outstaff`).
- `--region`, `--country` and `--city` form one OR group. `--region eu --country BR` means Europe or Brazil. To search one country, pass only `--country`.
- An empty query browses by filters alone.
- Each hit includes the full description as markdown. Select fields with `jq` and keep `--limit` small when you read descriptions.

Watch stderr. The API ignores unknown filters without failing, and the CLI prints `warning: ignored unknown filter ...`, often with a `did you mean`. Fix the filter and rerun before you quote any count.

Skill tags come from the description text. Treat `--exclude-skill` as a rough filter, not proof of the stack.

## 3. Open results

```bash
freehire --json search "golang" --limit 3 | jq '.[] | {title, company_slug, url, description}'
freehire --json company <company-slug> | jq '{name: .company.name, jobs: [.jobs[] | {title, public_slug}]}'
```

Each search hit already has the full description, the original `url` where the person applies, and the company slug.

## Presenting results

Link each job as `https://freehire.me/jobs/<public_slug>`, one per line. Say which filters you used, so the person can adjust them.
