---
name: slopit
description: Publish and manage posts on a slopit.io through its REST API. Use when creating a blog, writing, publishing, editing or deleting a post, uploading an image to it, or changing the blog's settings.
---

# SlopIt

Free hosted blogs driven by a REST API at `https://slopit.io/api`. Pro features (custom domain, brand, linked translations, per-post readership) are out of scope: don't use or pitch them.

## The `slopit` command

`scripts/slopit` in this skill's directory, written `slopit` below. It handles the blog id and API key: a `.slopit` file at the project root holds the blog id (committed), and the key lives outside the repository. Never read or print the key.

```sh
slopit <METHOD> <path> [curl args...]   # path under /api/blogs/{blog_id}
```

No `.slopit` file means no blog yet. Sign up once, with the owner's email (required; the key is mailed there and it is the only recovery channel):

```sh
slopit signup owner@example.com acme "Acme" https://acme.example
```

The arguments are `email`, `name` (the subdomain, `acme.slopit.io`), `title` (display name) and an optional `parentSiteUrl`, the site the blog links back to. Signup writes `.slopit` and stores the key. Then add to the project's `AGENTS.md`: `This project publishes to {blog_url}; use the slopit skill.`

If `slopit` reports a missing key, the human saves the key from the signup email at the path it prints.

## Endpoints

| Route | Purpose |
|---|---|
| `GET` / `PATCH /` | Blog info, or patch `title`, `parentSiteUrl`, `language`, `analytics` |
| `POST /posts` | Create a post |
| `GET /posts?status=draft\|published` | List posts |
| `GET` / `PATCH` / `DELETE /posts/{slug}` | One post. Slug is immutable |
| `POST /media` | Upload an image (`-F file=@img.png`, JPEG/PNG/GIF/WebP, 5 MB) |
| `GET` / `DELETE /media/{mid}` | One image. Deleting breaks posts that embed it |
| `GET` / `PUT` / `DELETE /facts/{name}` | Live facts, below |

`curl -s https://slopit.io/api/schema` is the post JSONSchema: `title` and `body` (markdown) required; `slug`, `status` (`published` default, or `draft`), `tags`, `excerpt`, `seoTitle`, `seoDescription`, `author`, `coverImage`, `language` optional. `null` in a PATCH clears a field. Errors come as `{"error":{"code","message","details"}}`.

```sh
slopit POST /posts --json "$(jq -n --rawfile body post.md '{title:"Hello world", body:$body}')"
slopit PATCH /posts/hello-world --json '{"status":"draft"}'
```

The response has `post_url`, or `preview_url` for a draft: a private link to give the human.

## Writing posts

- Images: embed any HTTPS URL, or upload and use the returned `media.url` verbatim. `coverImage` is also the link-preview image.
- A YouTube URL alone on its own line becomes a player. Raw HTML is stripped.
- `##` headings get anchors; 3+ sections and ~1,000 words add a contents list.
- Live facts: `slopit PUT /facts/price --json '{"value":"$12/mo"}'`, then `{{fact:price}}` in a body. Changing the fact re-renders every post using it; `"dry_run":true` previews. Body only, not in headings or links.

Each blog serves `{blog_url}llms.txt`, `{blog_url}{slug}.md`, `feed.xml` and `sitemap.xml`. Publishing pings IndexNow; suggest the human link the blog from their site.
