# PolyGlotty Launch Plan

## Launch Goal

Release PolyGlotty as a Telegram-first English learning product:

- Free users get a useful A0-C2 learning system: course, flashcards, drills, path, progress and daily practice.
- Paid users get ALEX Chat, AI explanations, advanced checks, more cards, higher quota and better models.
- The first public launch should validate retention and payment conversion before spending heavily on ads.

## What Is Ready

- Telegram bot with WebApp entry point.
- Railway deployment.
- PostgreSQL-backed user/progress/premium state.
- Telegram Stars subscriptions.
- Free A0-C2 course with 700 lessons.
- Level confirmation tests.
- Flashcards with rolling limits and topic preferences.
- Premium model gating and quota points.
- Daily tasks, streaks, progress and achievements.
- Basic support, payment support, terms and privacy commands.

## Required Before Public Traffic

### Product QA

- Open `/start` from a new Telegram account.
- Open the WebApp from Telegram mobile and desktop.
- Confirm the free user can use:
  - course;
  - flashcards;
  - drills;
  - path;
  - progress;
  - daily tasks.
- Confirm free user cannot access:
  - ALEX Chat;
  - AI explanations;
  - premium model picker;
  - premium-only session/error analytics.
- Confirm paid user can access:
  - ALEX Chat;
  - model picker;
  - voice features;
  - expanded cards;
  - premium tools.

### Payment QA

- Buy Basic monthly with Telegram Stars.
- Buy Pro monthly with Telegram Stars.
- Buy Ultimate monthly with Telegram Stars.
- Buy at least one yearly plan.
- Confirm subscription appears without restarting the bot.
- Confirm subscription stays after Railway restart.
- Confirm `/premium` shows current plan correctly.
- Confirm expired subscription returns the user to Free.
- Confirm `/paysupport` text is clear.

### Technical QA

- Check Railway logs after `/start`, `/premium`, `/api/chat`, `/api/premium/{uid}`.
- Check database writes for:
  - new user;
  - premium grant;
  - XP;
  - flashcard state;
  - level;
  - settings.
- Check there are no secrets committed to GitHub.
- Check `.env` is not in the repository.
- Check bot works after Railway redeploy.

### Content QA

- Review the first 20 lessons in A0, A1 and A2.
- Review at least 10 B1/B2 lessons.
- Review all visible Premium plan texts.
- Review TOEFL/IELTS tasks for English accuracy.
- Remove any text that sounds like test mode or internal debug.

## What Codex Can Do

- Fix bugs in bot, server, database and WebApp code.
- Improve UI and copy.
- Add or restructure course content.
- Add tests and local validation scripts.
- Add launch docs, support text and payment terms.
- Push changes to GitHub.
- Prepare daily content templates for Telegram/TikTok/Instagram.
- Analyze logs if pasted or available locally.
- Help calculate pricing and model limits.

## Owner Checklist

- Verify Telegram Stars payments from a real Telegram account.
- Decide final public prices.
- Decide final public plan names and feature wording.
- Add official bot avatar and channel avatar in Telegram.
- Set BotFather metadata if Telegram refuses automated profile updates.
- Create and configure the Telegram channel.
- Create TikTok, Instagram and Threads accounts.
- Post daily content for at least 14 days before paid ads.
- Monitor first users manually and collect feedback.
- Keep enough Anthropic balance for launch.

## Costs

### Required

- Railway: keep at least the $5/month Hobby setup active.
- Anthropic API balance: start with $30 minimum for testing and first users.

### Optional

- ElevenLabs: do not buy before proving that voice increases retention or conversion.
- Paid ads: start with $30-$100 only after payments are verified.
- Domain: optional for later landing page.
- Analytics/Sentry: optional; simple DB analytics is enough for first launch.

## Recommended Launch Budget

- Minimum: $35-$50.
- Comfortable first test: $80-$150.
- Serious first growth sprint: $200-$300.

## First 14 Days Growth Plan

### Daily Content

- Telegram channel: 1 word-of-the-day post.
- TikTok/Instagram/Threads: 1 carousel with 5 words or 1 short grammar tip.
- Every post should end with a calm CTA: `Учить больше: @PolyGlotty_bot`.

### Funnel

1. Social post teaches one small thing.
2. User opens Telegram bot.
3. Free user gets course/cards/progress.
4. ALEX Chat is visible but locked.
5. Upgrade message explains the benefit in one sentence.
6. Premium user gets immediate value from ALEX.

### Metrics To Watch

- New users per day.
- App opens per day.
- Users who complete at least 5 flashcards.
- Users who start the course.
- Users who hit the ALEX paywall.
- Premium purchases.
- Cost per paid user if ads are used.

## Launch Decision Rule

Launch publicly only when:

- payments work;
- free learning does not break;
- paid access activates correctly;
- support commands exist;
- plan texts match real limits;
- no obvious debug/test text remains;
- the first 10 organic testers can use the app without explanation.
