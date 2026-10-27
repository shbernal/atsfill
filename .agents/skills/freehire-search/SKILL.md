---
name: freehire-search
description: Use when searching for jobs on freehire.me with the `freehire` CLI, filtering by keyword, location, seniority, skills, salary or remote work, opening a posting, or listing a company's open roles.
---

# Searching jobs with freehire

`freehire` is a CLI over the [freehire.me](https://freehire.me) job catalogue. `search`, `facets`, `job` and `company` work without an account. Always pass `--json` and pull the fields you need with `jq -c`, since the JSON comes pretty-printed. The plain output is for people and drops fields.

If the command is missing, install it:

```bash
mise use -g github:strelov1/freehire-cli
```

## 1. Learn the filter values

Never guess a filter value. Read the live vocabulary first:

```bash
freehire --json facets | jq -c '.facets | keys'
freehire --json facets | jq -c '.facets.seniority'
freehire --json facets --category backend | jq -c '.facets.skills | to_entries | sort_by(-.value) | .[:20]'
```

Each facet lists its values with a vacancy count. Filters narrow the counts to that slice. Skills are lowercase slugs (`go`, `react`, `kubernetes`).

## 2. Search

```bash
freehire --json search "golang" --region eu --remote --seniority senior --limit 10 | jq -c '.[] | {title, company, location, public_slug}'
freehire --json search "" --country IT --employment-type contract --exclude-skill python | jq -c '.[] | {title, company, public_slug}'
freehire --json search 'title:golang OR title:rust -junior' --remote --facet reality=fresh --limit 5 | jq -c '.[] | {title, company, summary: .enrichment.summary, public_slug}'
```

The query takes operators. Single-quote the whole query when it contains double quotes, or the shell strips them.

- `"product owner"`: every quoted word must match, in any order.
- `-word` excludes postings that match it.
- `OR` (uppercase) joins up to 5 alternatives into one merged result. Use it instead of one call per synonym.
- `title:` at the start of an alternative matches only the title.
- Plain words are neither AND nor OR. Trailing words are dropped when too few postings match, so quote the words that must all appear.
- `--semantic` also matches postings that describe the query in other words. Use it for a need described in words (`"keep our payment systems secure"`). Leave it off for a job title or a non-technical role, where it does worse than plain keywords. `warning: semantic search unavailable` on stderr means the results are keyword-only.

- Repeat a filter flag to OR its values.
- `--facet key=value` reaches any facet without its own flag. Add `_exclude` to the key to filter values out (`--facet company_type_exclude=outstaff`).
- `--region`, `--country` and `--city` form one OR group. `--region eu --country BR` means Europe or Brazil. To search one country, pass only `--country`.
- An empty query browses by filters alone.
- `--facet reality=fresh` drops stale and evergreen postings.
- Each hit includes the full description as markdown. Screen with `.enrichment.summary` (sometimes null) and the `.enrichment.requirements` list, and read `description` only for the hits that survive.

Watch stderr. The API ignores unknown filters without failing, and the CLI prints `warning: ignored unknown filter ...`, often with a `did you mean`. Fix the filter and rerun before you quote any count.

Skill tags come from the description text. Treat `--exclude-skill` as a rough filter, not proof of the stack.

## 3. Open results

```bash
freehire job <public_slug>
freehire --json company <company-slug> | jq -c '{name: .company.name, jobs: [.jobs[] | {title, public_slug}]}'
```

Each search hit already has the full description, the original `url` where the person applies, and the company slug. Use `job` to read one posting as plain text. It also prints a `Possibly inactive` block when a posting looks like nobody is working it. Relay the checks that fired and the ones not observed, never call the job fake.

## Presenting results

Link each job as `https://freehire.me/jobs/<public_slug>`, one per line. Say which filters you used, so the person can adjust them.
