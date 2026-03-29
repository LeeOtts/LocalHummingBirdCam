/**
 * Backyard Hummers — Gallery Page JS
 * Renders clip grid from site_data.json, handles modal playback
 */

let allClips = [];

/**
 * Render a single gallery card
 */
function createGalleryCard(clip, index) {
    const card = document.createElement('div');
    card.className = 'gallery-card';
    card.dataset.index = index;

    const conf = clip.confidence ? Math.round(clip.confidence * 100) : 0;
    const thumbSrc = clip.thumbnail ? ('clips/' + clip.thumbnail) : '';
    const thumbHtml = thumbSrc
        ? `<img src="${thumbSrc}" alt="Hummingbird detection" loading="lazy">`
        : `<div class="gallery-thumb-placeholder">\uD83D\uDC26</div>`;

    card.innerHTML = `
        <div class="gallery-thumb">
            ${thumbHtml}
            <div class="target-brackets">
                <div class="bracket bracket-tl"></div>
                <div class="bracket bracket-tr"></div>
                <div class="bracket bracket-bl"></div>
                <div class="bracket bracket-br"></div>
            </div>
        </div>
        <div class="gallery-card-info">
            <div class="gallery-card-top">
                <span class="gallery-card-time">${formatTimestamp(clip.timestamp)}</span>
                <div class="confidence-bar">
                    <div class="confidence-fill" style="width: ${conf}%"></div>
                </div>
            </div>
            <p class="gallery-card-caption">${clip.caption || 'No caption available.'}</p>
        </div>
    `;

    card.addEventListener('click', () => openModal(clip));
    return card;
}

/**
 * Render the gallery grid
 */
function renderGallery(clips) {
    const grid = document.getElementById('galleryGrid');
    const empty = document.getElementById('galleryEmpty');
    const count = document.getElementById('clipCount');

    if (!grid) return;
    grid.innerHTML = '';

    if (!clips || clips.length === 0) {
        grid.style.display = 'none';
        if (empty) empty.style.display = 'block';
        if (count) count.textContent = '0';
        return;
    }

    grid.style.display = 'grid';
    if (empty) empty.style.display = 'none';
    if (count) count.textContent = clips.length;

    clips.forEach((clip, i) => {
        grid.appendChild(createGalleryCard(clip, i));
    });
}

/**
 * Open the video modal
 */
function openModal(clip) {
    const modal = document.getElementById('videoModal');
    const video = document.getElementById('modalVideo');
    const timestamp = document.getElementById('modalTimestamp');
    const caption = document.getElementById('modalCaption');
    const confidence = document.getElementById('modalConfidence');
    const platforms = document.getElementById('modalPlatforms');

    if (!modal || !video) return;

    video.querySelector('source').src = 'clips/' + clip.filename;
    video.load();

    if (timestamp) timestamp.textContent = formatTimestamp(clip.timestamp);
    if (caption) caption.textContent = clip.caption || 'No intel available.';
    if (confidence) {
        const conf = clip.confidence ? Math.round(clip.confidence * 100) : '--';
        confidence.textContent = `CONFIDENCE: ${conf}%`;
    }
    if (platforms) platforms.innerHTML = renderPlatformBadges(clip.platforms_posted);

    modal.style.display = 'flex';
    document.body.style.overflow = 'hidden';
}

/**
 * Close the video modal
 */
function closeModal() {
    const modal = document.getElementById('videoModal');
    const video = document.getElementById('modalVideo');

    if (modal) modal.style.display = 'none';
    if (video) video.pause();
    document.body.style.overflow = '';
}

/**
 * Filter clips by date
 */
function filterByDate(dateStr) {
    if (!dateStr) {
        renderGallery(allClips);
        return;
    }
    const filtered = allClips.filter(clip => {
        try {
            return clip.timestamp.startsWith(dateStr);
        } catch {
            return false;
        }
    });
    renderGallery(filtered);
}

/**
 * Initialize gallery page
 */
document.addEventListener('DOMContentLoaded', async () => {
    // Wait for app.js to load data
    if (!siteData) {
        await loadSiteData();
    }
    const data = siteData;

    if (data && data.clips) {
        allClips = data.clips;
        renderGallery(allClips);
    } else {
        renderGallery([]);
    }

    // Date filter
    const dateFilter = document.getElementById('dateFilter');
    if (dateFilter) {
        dateFilter.addEventListener('change', (e) => filterByDate(e.target.value));
    }

    // Modal close handlers
    const modalClose = document.getElementById('modalClose');
    const modalBackdrop = document.getElementById('modalBackdrop');
    if (modalClose) modalClose.addEventListener('click', closeModal);
    if (modalBackdrop) modalBackdrop.addEventListener('click', closeModal);

    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') closeModal();
    });
});
