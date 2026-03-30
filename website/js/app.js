/**
 * Backyard Hummers — Shared JS
 * Handles: data loading, nav, live feed, clock, social links, landing page
 */

const DATA_URL = 'data/site_data.json';

// Social platform icons — Simple Icons CDN (colored SVGs)
const PLATFORM_ICONS = {
    bluesky:   { icon: '<img src="https://cdn.simpleicons.org/bluesky/0085FF" alt="Bluesky" class="social-logo">', label: 'Bluesky' },
    facebook:  { icon: '<img src="https://cdn.simpleicons.org/facebook/1877F2" alt="Facebook" class="social-logo">', label: 'Facebook' },
    github:    { icon: '<img src="https://cdn.simpleicons.org/github/ffffff" alt="GitHub" class="social-logo">', label: 'GitHub' },
    instagram: { icon: '<img src="https://cdn.simpleicons.org/instagram/E4405F" alt="Instagram" class="social-logo">', label: 'Instagram' },
    tiktok:    { icon: '<img src="https://cdn.simpleicons.org/tiktok/ffffff" alt="TikTok" class="social-logo">', label: 'TikTok' },
    twitter:   { icon: '<img src="https://cdn.simpleicons.org/x/ffffff" alt="X / Twitter" class="social-logo">', label: 'X / Twitter' },
};

// Static social links — hardcoded, always shown
const STATIC_SOCIALS = {
    bluesky:   'https://bsky.app/profile/backyardhummers.com',
    facebook:  'https://facebook.com/backyard.hummers',
    github:    'https://github.com/LeeOtts/LocalHummingBirdCam',
    instagram: 'https://instagram.com/backyard.hummers',
    tiktok:    'https://tiktok.com/@backyardhummers',
    twitter:   'https://x.com/backyardhummers',
};

let siteData = null;

/**
 * Fetch site_data.json
 */
async function loadSiteData() {
    try {
        const resp = await fetch(DATA_URL + '?t=' + Date.now());
        if (!resp.ok) throw new Error('Failed to load data');
        siteData = await resp.json();
        return siteData;
    } catch (err) {
        console.warn('Could not load site data:', err);
        return null;
    }
}

/**
 * Format a timestamp for display (military style)
 */
function formatTimestamp(isoStr) {
    try {
        const d = new Date(isoStr);
        const hours = String(d.getHours()).padStart(2, '0');
        const mins = String(d.getMinutes()).padStart(2, '0');
        const secs = String(d.getSeconds()).padStart(2, '0');
        const month = String(d.getMonth() + 1).padStart(2, '0');
        const day = String(d.getDate()).padStart(2, '0');
        const year = d.getFullYear();
        return `${year}-${month}-${day} ${hours}:${mins}:${secs} CST`;
    } catch {
        return isoStr || '--';
    }
}

/**
 * Format a time-only display
 */
function formatTime(isoStr) {
    try {
        const d = new Date(isoStr);
        let hours = d.getHours();
        const mins = String(d.getMinutes()).padStart(2, '0');
        const ampm = hours >= 12 ? 'PM' : 'AM';
        hours = hours % 12 || 12;
        return `${hours}:${mins} ${ampm}`;
    } catch {
        return '--';
    }
}

/**
 * Render platform badges
 */
function renderPlatformBadges(platforms) {
    if (!platforms || !platforms.length) return '';
    return platforms.map(p => {
        const info = PLATFORM_ICONS[p] || { icon: '', label: p };
        return `<span class="platform-badge">${info.icon} ${info.label}</span>`;
    }).join('');
}

/**
 * Update the live clock
 */
function startClock() {
    const el = document.getElementById('currentTime');
    if (!el) return;
    function tick() {
        const now = new Date();
        const h = String(now.getHours()).padStart(2, '0');
        const m = String(now.getMinutes()).padStart(2, '0');
        const s = String(now.getSeconds()).padStart(2, '0');
        el.textContent = `${h}:${m}:${s} CST`;
    }
    tick();
    setInterval(tick, 1000);
}

/**
 * Set up live feed iframe
 */
function setupLiveFeed(data) {
    const img = document.getElementById('cameraFeed');
    const offline = document.getElementById('feedOffline');
    if (!img || !data || !data.live_feed_url) {
        if (img) img.style.display = 'none';
        if (offline) offline.style.display = 'flex';
        return;
    }

    // MJPEG streams natively in <img> tags across all browsers
    img.style.display = 'block';
    img.src = data.live_feed_url;

    // Show offline message if stream fails to load
    img.addEventListener('error', () => {
        img.style.display = 'none';
        if (offline) offline.style.display = 'flex';
    });
}

/**
 * Populate stats ticker on homepage
 */
function populateStatsTicker(data) {
    if (!data) return;

    const lifetime = document.getElementById('lifetimeCount');
    const today = document.getElementById('todayCount');
    const week = document.getElementById('weekCount');
    const next = document.getElementById('nextVisit');

    if (lifetime) lifetime.textContent = (data.lifetime_detections || 0).toLocaleString();
    if (today) today.textContent = data.today_detections || 0;
    if (week) week.textContent = data.this_week_detections || 0;

    if (next && data.next_predicted_visit) {
        next.textContent = formatTime(data.next_predicted_visit.time);
    }
}

/**
 * Populate latest detection on homepage
 */
function populateLatestDetection(data) {
    if (!data || !data.clips || !data.clips.length) return;

    const clip = data.clips[0]; // Most recent
    const video = document.getElementById('latestVideo');
    const timestamp = document.getElementById('latestTimestamp');
    const caption = document.getElementById('latestCaption');
    const confidence = document.getElementById('latestConfidence');
    const platforms = document.getElementById('latestPlatforms');

    if (video) {
        video.querySelector('source').src = 'clips/' + clip.filename;
        video.poster = clip.thumbnail ? ('clips/' + clip.thumbnail) : '';
        video.load();
    }
    if (timestamp) timestamp.textContent = formatTimestamp(clip.timestamp);
    if (caption) caption.textContent = clip.caption || 'No intel available.';
    if (confidence) {
        const conf = clip.confidence ? Math.round(clip.confidence * 100) : '--';
        confidence.textContent = `CONFIDENCE: ${conf}%`;
    }
    if (platforms) platforms.innerHTML = renderPlatformBadges(clip.platforms_posted);
}

/**
 * Populate social links
 */
function populateSocials(data) {
    const grid = document.getElementById('socialsGrid');
    if (!grid) return;

    const socials = { ...STATIC_SOCIALS };
    const sorted = Object.entries(socials)
        .filter(([, url]) => url)
        .sort(([a], [b]) => {
            const labelA = (PLATFORM_ICONS[a] || { label: a }).label;
            const labelB = (PLATFORM_ICONS[b] || { label: b }).label;
            return labelA.localeCompare(labelB);
        });

    grid.innerHTML = '';
    for (const [platform, url] of sorted) {
        const info = PLATFORM_ICONS[platform] || { icon: '', label: platform };
        const card = document.createElement('a');
        card.href = url;
        card.target = '_blank';
        card.rel = 'noopener noreferrer';
        card.className = 'social-card';
        card.innerHTML = `
            <span class="social-icon">${info.icon}</span>
            <span class="social-name">${info.label}</span>
        `;
        grid.appendChild(card);
    }
}

/**
 * Mobile nav toggle
 */
function setupNav() {
    const hamburger = document.querySelector('.nav-hamburger');
    const links = document.querySelector('.nav-links');
    if (!hamburger || !links) return;

    hamburger.addEventListener('click', () => {
        links.classList.toggle('open');
    });

    // Close on link click
    links.querySelectorAll('.nav-link').forEach(link => {
        link.addEventListener('click', () => links.classList.remove('open'));
    });
}

/**
 * Update HUD status indicators (sleeping / watering)
 */
function updateHudStatus(data) {
    if (!data) return;
    const sleeping = document.getElementById('hudSleeping');
    const watering = document.getElementById('hudWatering');
    if (sleeping) sleeping.style.display = data.sleeping ? 'flex' : 'none';
    if (watering) watering.style.display = data.sprinkler_active ? 'flex' : 'none';
}

/**
 * Initialize
 */
document.addEventListener('DOMContentLoaded', async () => {
    setupNav();
    startClock();

    const data = await loadSiteData();
    if (!data) return;

    // Homepage-specific
    setupLiveFeed(data);
    populateStatsTicker(data);
    populateLatestDetection(data);
    populateSocials(data);
    updateHudStatus(data);
});
