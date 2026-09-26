"""Terms of service, refund and refill policy, privacy policy.

Written to match how the site actually works (wallet ledger, automatic refunds, refill rules,
top-up expiry). Change the code, change these too.
"""
from app.config import get_settings
from app.payments import TOPUP_TTL_MINUTES

UPDATED = "September 26, 2026"
CONTACT = '<!--email_off--><a href="mailto:support@smmshiro.com">support@smmshiro.com</a><!--/email_off-->'


def _wrap(kicker: str, h1: str, inner: str) -> str:
    return f"""<section class="block legal">
  <div class="wrap prose">
    <span class="kicker">{kicker}</span>
    <h1>{h1}</h1>
    <p class="updated">Last updated {UPDATED}</p>
    {inner}
    <p class="legal-nav"><a href="/terms/">Terms of service</a> · <a href="/refund-policy/">Refund and refill policy</a> · <a href="/privacy/">Privacy policy</a></p>
  </div>
</section>"""


def terms() -> str:
    s = get_settings()
    pct = f"{s.referral_pct:g}"
    return _wrap("Legal", "Terms of service", f"""
<p>These terms are an agreement between you and SMM Shiro ("we", "us"), the operator of smmshiro.com. By creating an account, topping up or placing an order you agree to them, and to our <a href="/refund-policy/">Refund and refill policy</a> and <a href="/privacy/">Privacy policy</a>. If you don't agree, don't use the site.</p>

<h2>1. What we sell</h2>
<p>SMM Shiro is a social media marketing (SMM) panel. We resell engagement services, such as followers, likes, views, comments and members, for platforms including TikTok, Facebook, Instagram, YouTube, Telegram and X. Services are delivered by third-party suppliers. We are not affiliated with, endorsed by or sponsored by any of these platforms.</p>
<p>Each service shows its quality label, usual start time, speed, drop risk and refill terms before you order. These are our best description of the service, not a promise of a particular outcome. We do not guarantee more reach, sales, monetization, verification or any other result from an order.</p>

<h2>2. Your account</h2>
<ul>
  <li>You must be at least 18, or have a parent or guardian's permission to use the site.</li>
  <li>Keep your password private. You are responsible for everything done with your account and your API key.</li>
  <li>One person may hold more than one account only if they don't use them to abuse promotions or the referral program.</li>
  <li>We never ask for the password of your social media accounts. Anyone who does is not us.</li>
</ul>

<h2>3. Wallet and top-ups</h2>
<ul>
  <li>Orders are paid from your wallet balance, in Philippine pesos. You add balance by topping up with QR Ph through PayMongo's checkout. Top-ups are from ₱{s.topup_min_php:,} to ₱{s.topup_max_php:,}.</li>
  <li>A top-up checkout stays open for {TOPUP_TTL_MINUTES} minutes. You can cancel it before you pay. If a payment still goes through after a checkout has expired or been canceled, we credit it to your balance.</li>
  <li>Free credit we give, such as a welcome credit for new accounts, can only be used for orders. It can't be refunded or withdrawn, and it's one per person: accounts made to collect it again may be closed.</li>
  <li>Your balance is credit for use on this site. It is not a deposit or e-money account, earns no interest and cannot be transferred to another account. See the <a href="/refund-policy/">Refund and refill policy</a> for when it can be refunded.</li>
</ul>

<h2>4. Placing orders</h2>
<ul>
  <li>The price you see when you order is the price you pay. Prices can change at any time, but never for an order you've already placed.</li>
  <li>Give a correct, public link. The profile, post or video must stay public, and the link and username must not change, until the order completes. If they do, the order may complete without full delivery and without a refund for the missed part.</li>
  <li>Don't run two orders for the same service type on the same link at the same time, whether here or on another panel. The counts get mixed and we cannot tell which order delivered what, so such orders are not eligible for refunds or refills of the overlap.</li>
  <li>Orders can't be edited after they are placed. Some services can be canceled while pending or in progress; the button shows only where the supplier allows it, and a cancel request can be declined.</li>
  <li>Social platforms remove engagement from time to time. Drops after delivery are covered only by a service's refill period.</li>
</ul>

<h2>5. What you may not do</h2>
<p>Don't order for content that is illegal, sexual content involving minors, content promoting violence, hate, scams or gambling that is illegal in the Philippines, or content you don't have the right to promote. Don't use the site to harass anyone, to manipulate elections or public health information, or to break the law. Don't attack, overload, scrape or reverse-engineer the site outside the documented API. You are responsible for making sure your use of our services complies with the terms of the platforms you order for.</p>

<h2>6. Reseller API</h2>
<p>You may resell our services through the API. You are responsible for your own customers, and they have no agreement with us. Keep your API key secret. We may limit request rates, and we may revoke a key that is abused.</p>

<h2>7. Referral program</h2>
<p>When someone creates an account through your referral link, you earn {pct}% of each top-up they complete, added to your wallet balance as credit. Referring yourself or your own other accounts is not allowed. We may withhold or reverse commissions that come from fraud, chargebacks, or abuse, and we may change the rate or end the program; commissions already credited stay yours.</p>

<h2>8. Suspension and closing</h2>
<p>We may suspend or close an account that breaks these terms, is used for fraud, or puts the site or its suppliers at risk. If we close an account for any other reason, we refund its remaining balance. You can close your account at any time by emailing us.</p>

<h2>9. Liability</h2>
<p>The site and the services are provided as they are. To the extent the law allows, we are not liable for indirect losses, such as lost profits or a platform suspending your account, and our total liability for any claim is limited to the amount you paid for the order it concerns. Nothing in these terms limits rights you have under Philippine consumer law that cannot be waived.</p>

<h2>10. Changes and law</h2>
<p>We may update these terms. The date above shows the latest version, and the version in force when you place an order applies to that order. These terms are governed by the laws of the Republic of the Philippines. We will try to settle any dispute with you directly first; write to {CONTACT}.</p>
""")


def refund_policy() -> str:
    return _wrap("Legal", "Refund and refill policy", f"""
<p>This policy explains when money goes back to your wallet and when you can ask for a refill. It is part of our <a href="/terms/">Terms of service</a>.</p>

<h2>Automatic refunds</h2>
<ul>
  <li><strong>Canceled orders.</strong> If an order is canceled, by you or by the supplier, whatever wasn't delivered is refunded to your wallet balance.</li>
  <li><strong>Partial orders.</strong> If an order ends up partial, the undelivered part is refunded to your balance, in proportion to what was not delivered.</li>
  <li><strong>Failed orders.</strong> If an order can't be placed with the supplier, the full charge is refunded.</li>
</ul>
<p>These refunds happen automatically, usually within a few minutes of the status change. You'll see them in your Orders page under Refunded, and in your balance.</p>

<h2>Refills</h2>
<p>Some services come with a refill period, shown on the service as, for example, "30-day refill". If part of what was delivered drops during that period, you can request a refill from your Orders page. A refill tops the count back up to what was delivered; it isn't a refund.</p>
<ul>
  <li>The refill period starts when the order completes.</li>
  <li>The Refill button shows only for completed orders on services with refill, while the period is running. Only one refill can be in progress per order at a time.</li>
  <li>Refills are not available if the link or username changed, the account or post went private or was removed, or the same link was also ordered elsewhere at the same time.</li>
  <li>Services marked "No refill" are not refilled or refunded for drops.</li>
</ul>

<h2>Wallet balance</h2>
<p>Balance is credit for orders on this site. We don't normally pay it out as cash. We do refund top-ups back to you in these cases:</p>
<ul>
  <li>You were charged twice, or charged without the top-up reaching your balance, and it can't be credited.</li>
  <li>We close your account for a reason other than a breach of the Terms.</li>
  <li>The law requires it.</li>
</ul>
<p>Commission earned through the referral program is credit only and is not paid out as cash.</p>

<h2>Top-up problems</h2>
<p>If you paid and your balance didn't update within 30 minutes, email {CONTACT} with your account email, the amount and the reference number from your GCash, Maya or bank app. We check every payment against PayMongo's records.</p>

<h2>Chargebacks</h2>
<p>If you dispute a payment with your bank or wallet provider instead of contacting us, we may suspend the account while the dispute is open and deduct the disputed amount from its balance.</p>
""")


def privacy() -> str:
    return _wrap("Legal", "Privacy policy", f"""
<p>This policy explains what personal data SMM Shiro collects, why, and what rights you have under the Philippine Data Privacy Act of 2012 (Republic Act No. 10173). For questions or requests, email {CONTACT}.</p>

<h2>What we collect</h2>
<ul>
  <li><strong>Account:</strong> your email address and your password, stored only as a one-way hash (Argon2). We cannot read your password.</li>
  <li><strong>Orders:</strong> the links you submit, the services, quantities and any custom comments you enter, and their status.</li>
  <li><strong>Payments:</strong> top-up amounts, status and PayMongo's reference. You pay on PayMongo's checkout; we never see your GCash, Maya, bank or card details.</li>
  <li><strong>Wallet:</strong> a record of every change to your balance (top-ups, orders, refunds, referral commission).</li>
  <li><strong>Referrals and API:</strong> who referred you, if anyone, and a hash of your API key if you create one.</li>
  <li><strong>Technical data:</strong> your IP address and browser details, which our hosting and security providers log, and which we use to limit repeated failed logins.</li>
</ul>

<h2>Why we use it</h2>
<ul>
  <li>To run your account, deliver orders, process top-ups, refunds and refills (performance of our agreement with you).</li>
  <li>To keep the site secure and prevent fraud and abuse (legitimate interest).</li>
  <li>To keep financial records as tax and accounting law requires (legal obligation).</li>
  <li>To answer you when you contact support.</li>
</ul>
<p>Logged-in customers can see a "Recently completed" list of orders delivered on the site. It shows only the service, quantity and delivery time, never your link, account, email or name.</p>
<p>We don't sell your data, we don't use advertising trackers or third-party analytics, and we don't send marketing email.</p>

<h2>Who we share it with</h2>
<p>Only the service providers that run the site, each for its own part:</p>
<ul>
  <li><strong>PayMongo</strong> (Philippines): payment processing.</li>
  <li><strong>Our SMM suppliers:</strong> the link, quantity and comments of each order, which they need to deliver it. Never your email or name.</li>
  <li><strong>Render</strong> (United States): application hosting.</li>
  <li><strong>Neon</strong> (United States): database hosting.</li>
  <li><strong>Cloudflare</strong> (global): domain, security and content delivery.</li>
  <li><strong>Google Fonts:</strong> your browser loads our fonts from Google, which sees your IP address.</li>
</ul>
<p>Some of these providers store data outside the Philippines. We use providers that protect data with encryption in transit and at rest. We will also disclose data when the law requires it.</p>

<h2>Cookies and local storage</h2>
<ul>
  <li><code>session</code>: keeps you logged in. Strictly necessary; set only after you log in or sign up.</li>
  <li><code>theme</code> (local storage): remembers light or dark mode on your device.</li>
</ul>
<p>We use no advertising or tracking cookies.</p>

<h2>How long we keep it</h2>
<p>We keep account and order data while your account is open. When you ask us to delete your account, we delete or anonymize your email, links and comments, and keep only the payment and wallet records that tax and accounting law requires us to keep, for as long as it requires.</p>

<h2>Your rights</h2>
<p>Under the Data Privacy Act you have the right to be informed, to access your data, to object, to correct it, to have it erased or blocked, to data portability, and to claim damages. Email {CONTACT} to use any of them; we reply within 15 days. If you're not satisfied, you can complain to the <a href="https://privacy.gov.ph" rel="noopener">National Privacy Commission</a>.</p>

<h2>Security</h2>
<p>Connections are encrypted (HTTPS), passwords are hashed, login attempts are rate limited, and access to the database is restricted. No system is perfectly secure; if a breach affects your data, we will notify you and the National Privacy Commission as the law requires.</p>

<h2>Children</h2>
<p>The site is not meant for children under 18 without a parent or guardian's permission.</p>

<h2>Changes</h2>
<p>We will post changes here and update the date above. Significant changes will also be announced on the site.</p>
""")
