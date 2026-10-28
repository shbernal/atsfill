---
name: slopit
description: Publish and manage posts on a slopit.io through its REST API. Use when creating a blog, writing, publishing, editing or deleting a post, uploading an image to it, or changing the blog's settings.
---

# SlopIt

Free hosted blogs driven by a REST API at `https://slopit.io/api`. Pro features (custom domain, brand, linked translations, per-post readership) are out of scope: don't use or pitch them.

## API key

One key per blog, stored at `~/.config/slopit/<blog_id>`, never in the repository. The project's `AGENTS.md` records which blog it uses:

```markdown
## Blog
SlopIt blog {blog_url}, id {blog_id}. Key in ~/.config/slopit/{blog_id}. Use the slopit skill.
```

Load it per request:

```sh
id=abc12345; auth="Authorization: Bearer $(cat ~/.config/slopit/$id)"
```

No note in `AGENTS.md` means no blog yet. Sign up once, with the owner's email (required; the key is mailed there and it is the only recovery channel):

```sh
curl -s https://slopit.io/api/signup -H 'Content-Type: application/json' \
  -d '{"email":"owner@example.com","name":"acme","title":"Acme","parentSiteUrl":"https://acme.example"}' \
  | tee /dev/stderr | jq -r '"\(.blog_id) \(.api_key)"' \
  | { read id key; mkdir -p ~/.config/slopit; (umask 077; echo "$key" > ~/.config/slopit/$id); }
```

`name` is the subdomain (`acme.slopit.io`, else `slopit.io/b/<blog_id>/`), `title` the display name. Retrying signup creates another blog, so save the first response. Then add the note to `AGENTS.md`.

## Endpoints

All under `https://slopit.io/api/blogs/{blog_id}`, with `-H "$auth"`:

| Route | Purpose |
|---|---|
| `GET` / `PATCH` | Blog info, or patch `title`, `parentSiteUrl`, `language`, `analytics` |
| `POST /posts` | Create a post |
| `GET /posts?status=draft\|published` | List posts |
| `GET` / `PATCH` / `DELETE /posts/{slug}` | One post. Slug is immutable |
| `POST /media` | Upload an image (multipart `file`, JPEG/PNG/GIF/WebP, 5 MB) |
| `GET` / `DELETE /media/{mid}` | One image. Deleting breaks posts that embed it |
| `GET` / `PUT` / `DELETE /facts/{name}` | Live facts, below |

`GET https://slopit.io/api/schema` is the post JSONSchema: `title` and `body` (markdown) required; `slug`, `status` (`published` default, or `draft`), `tags`, `excerpt`, `seoTitle`, `seoDescription`, `author`, `coverImage`, `language` optional. `null` in a PATCH clears a field. Errors come as `{"error":{"code","message","details"}}`.

```sh
curl -s "https://slopit.io/api/blogs/$id/posts" -H "$auth" -H 'Content-Type: application/json' \
  -d "$(jq -n --rawfile body post.md '{title:"Hello world", body:$body}')"
```

The response has `post_url`, or `preview_url` for a draft: a private link to give the human.

## Writing posts

- Images: embed any HTTPS URL, or upload and use the returned `media.url` verbatim. `coverImage` is also the link-preview image.
- A YouTube URL alone on its own line becomes a player. Raw HTML is stripped.
- `##` headings get anchors; 3+ sections and ~1,000 words add a contents list.
- Live facts: `PUT /facts/price` with `{"value":"$12/mo"}`, then `{{fact:price}}` in a body. Changing the fact re-renders every post using it; `"dry_run":true` previews. Body only, not in headings or links.

Each blog serves `{blog_url}llms.txt`, `{blog_url}{slug}.md`, `feed.xml` and `sitemap.xml`. Publishing pings IndexNow; suggest the human link the blog from their site.
