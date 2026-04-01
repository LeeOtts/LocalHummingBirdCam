/**
 * Backyard Hummers — Stats Page JS
 * Renders Chart.js charts and stat cards from site_data.json
 */

// Chart.js global defaults for the surveillance theme (set before first chart render)
function setChartDefaults() {
    if (typeof Chart === 'undefined') return;
    Chart.defaults.color = '#9098a8';
    Chart.defaults.borderColor = '#2a3648';
    Chart.defaults.font.family = "'JetBrains Mono', monospace";
    Chart.defaults.font.size = 11;
}

/**
 * Render the hourly distribution bar chart
 */
function renderHourlyChart(hourlyPattern) {
    const ctx = document.getElementById('hourlyChart');
    if (!ctx || !hourlyPattern) return;

    const labels = [];
    for (let i = 0; i < 24; i++) {
        const h = i % 12 || 12;
        const ampm = i < 12 ? 'A' : 'P';
        labels.push(`${h}${ampm}`);
    }

    // Pad array to 24 hours
    const data = Array.isArray(hourlyPattern) ? hourlyPattern : [];
    while (data.length < 24) data.push(0);

    new Chart(ctx, {
        type: 'bar',
        data: {
            labels,
            datasets: [{
                label: 'Detections',
                data: data,
                backgroundColor: data.map((v, i) => {
                    const max = Math.max(...data);
                    const ratio = max > 0 ? v / max : 0;
                    if (ratio > 0.8) return '#d4a017';
                    if (ratio > 0.5) return '#5cb84c';
                    return 'rgba(92, 184, 76, 0.4)';
                }),
                borderRadius: 3,
                borderSkipped: false,
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: true,
            aspectRatio: 2.5,
            plugins: {
                legend: { display: false },
                tooltip: {
                    callbacks: {
                        title: (items) => {
                            const i = items[0].dataIndex;
                            const h = i % 12 || 12;
                            const ampm = i < 12 ? 'AM' : 'PM';
                            return `${h}:00 ${ampm}`;
                        },
                        label: (item) => `${item.raw} detections`
                    }
                }
            },
            scales: {
                y: {
                    beginAtZero: true,
                    ticks: { precision: 0 },
                    grid: { color: 'rgba(42, 54, 72, 0.5)' }
                },
                x: {
                    grid: { display: false }
                }
            }
        }
    });
}

/**
 * Render the daily trend line chart
 */
function renderDailyChart(dailyCounts) {
    const ctx = document.getElementById('dailyChart');
    if (!ctx || !dailyCounts || !dailyCounts.length) return;

    const labels = dailyCounts.map(d => {
        try {
            const date = new Date(d.date + 'T00:00:00');
            return `${date.getMonth() + 1}/${date.getDate()}`;
        } catch {
            return d.date;
        }
    });

    const data = dailyCounts.map(d => d.total_detections || 0);

    new Chart(ctx, {
        type: 'line',
        data: {
            labels,
            datasets: [{
                label: 'Daily Detections',
                data: data,
                borderColor: '#5cb84c',
                backgroundColor: 'rgba(92, 184, 76, 0.1)',
                fill: true,
                tension: 0.3,
                pointRadius: 3,
                pointHoverRadius: 6,
                pointBackgroundColor: '#5cb84c',
                pointBorderColor: '#1e2a3a',
                pointBorderWidth: 2,
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: true,
            aspectRatio: 2.5,
            plugins: {
                legend: { display: false },
                tooltip: {
                    callbacks: {
                        label: (item) => `${item.raw} detections`
                    }
                }
            },
            scales: {
                y: {
                    beginAtZero: true,
                    ticks: { precision: 0 },
                    grid: { color: 'rgba(42, 54, 72, 0.5)' }
                },
                x: {
                    grid: { display: false },
                    ticks: {
                        maxTicksLimit: 10,
                        maxRotation: 0
                    }
                }
            }
        }
    });
}

/**
 * Populate stat cards
 */
function populateStats(data) {
    if (!data) return;

    // Key metrics
    const el = (id) => document.getElementById(id);

    const lifetime = el('statLifetime');
    if (lifetime) lifetime.textContent = (data.lifetime_detections || 0).toLocaleString();

    const today = el('statToday');
    if (today) today.textContent = data.today_detections || 0;

    const week = el('statWeek');
    if (week) week.textContent = data.this_week_detections || 0;

    // Next visit prediction
    const nextVisit = el('statNextVisit');
    const nextConf = el('statNextConfidence');
    if (nextVisit && data.next_predicted_visit) {
        nextVisit.textContent = formatTime(data.next_predicted_visit.time);
        if (nextConf) {
            const conf = (data.next_predicted_visit.confidence || 'unknown').toUpperCase();
            nextConf.textContent = `CONFIDENCE: ${conf}`;
        }
    }

    // Milestone progress
    if (data.milestones) {
        const fill = el('milestoneFill');
        const label = el('milestoneLabel');
        const latest = data.milestones.latest || 0;
        const next = data.milestones.next || 1000;
        const progress = next > 0 ? Math.min((data.lifetime_detections / next) * 100, 100) : 0;
        if (fill) fill.style.width = progress + '%';
        if (label) label.textContent = `Next milestone: ${next.toLocaleString()}`;
    }

    // Summary stats from hourly pattern
    if (data.hourly_pattern && data.hourly_pattern.some(v => v > 0)) {
        const hourly = data.hourly_pattern;
        const maxVal = Math.max(...hourly);
        const peakIdx = hourly.indexOf(maxVal);
        const peakHour = el('peakHour');
        if (peakHour && maxVal > 0) {
            const h = peakIdx % 12 || 12;
            const ampm = peakIdx < 12 ? 'AM' : 'PM';
            peakHour.textContent = `${h}:00 ${ampm}`;
        }
    }

    // Summary stats from daily counts
    if (data.daily_counts_30d && data.daily_counts_30d.length) {
        const daily = data.daily_counts_30d;
        const totals = daily.map(d => d.total_detections || 0);

        // Busiest day
        const maxDay = Math.max(...totals);
        const maxDayIdx = totals.indexOf(maxDay);
        const busiestDay = el('busiestDay');
        if (busiestDay && maxDay > 0) {
            try {
                const d = new Date(daily[maxDayIdx].date + 'T00:00:00');
                const days = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
                busiestDay.textContent = `${days[d.getDay()]} (${maxDay})`;
            } catch {
                busiestDay.textContent = `${maxDay} detections`;
            }
        }

        // Daily average
        const avg = totals.reduce((a, b) => a + b, 0) / totals.length;
        const dailyAvg = el('dailyAvg');
        if (dailyAvg) dailyAvg.textContent = avg.toFixed(1);
    }

    // Average gap
    if (data.avg_gap_minutes) {
        const avgGap = el('avgGap');
        if (avgGap) {
            const mins = Math.round(data.avg_gap_minutes);
            if (mins >= 60) {
                avgGap.textContent = `${Math.floor(mins / 60)}h ${mins % 60}m`;
            } else {
                avgGap.textContent = `${mins} min`;
            }
        }
    }
}

/**
 * Format a YYYY-MM-DD date string to a readable display
 */
function formatSeasonDate(dateStr) {
    if (!dateStr) return '--';
    try {
        const d = new Date(dateStr + 'T00:00:00');
        const months = ['January','February','March','April','May','June',
                        'July','August','September','October','November','December'];
        return `${months[d.getMonth()]} ${d.getDate()}, ${d.getFullYear()}`;
    } catch {
        return dateStr;
    }
}

/**
 * Calculate days between two YYYY-MM-DD date strings
 */
function seasonLength(first, last) {
    if (!first || !last) return null;
    try {
        const d1 = new Date(first + 'T00:00:00');
        const d2 = new Date(last + 'T00:00:00');
        return Math.round((d2 - d1) / (1000 * 60 * 60 * 24));
    } catch {
        return null;
    }
}

/**
 * Render season prediction and history from site_data.json
 */
function renderSeasonData(data) {
    const seasons = data.season_dates;
    if (!seasons || !seasons.length) return;

    // Season History Table
    const section = document.getElementById('seasonHistorySection');
    const tbody = document.getElementById('seasonTableBody');
    if (section && tbody) {
        tbody.innerHTML = '';
        for (const s of seasons) {
            const len = seasonLength(s.first_visit, s.last_visit);
            const tr = document.createElement('tr');
            tr.style.borderBottom = '1px solid var(--border)';
            tr.innerHTML = `
                <td style="padding:10px; font-weight:bold;">${s.year}</td>
                <td style="padding:10px; color:#5cb84c;">${formatSeasonDate(s.first_visit)}</td>
                <td style="padding:10px; color:#fff;">${formatSeasonDate(s.last_visit)}</td>
                <td style="padding:10px; color:var(--text-muted);">${len !== null ? len + ' days' : '--'}</td>
            `;
            tbody.appendChild(tr);
        }
        section.style.display = '';
    }

    // Season Arrival Prediction (pre-computed server-side)
    const pred = data.season_prediction;
    if (!pred) return;

    const predSection = document.getElementById('seasonPredictionSection');
    const predContent = document.getElementById('seasonPredictionContent');
    if (!predSection || !predContent) return;

    if (pred.in_season) {
        predContent.innerHTML = `
            <span class="metric-value" style="color:#5cb84c;">Season is Active!</span>
            ${pred.avg_season_length_days ? `<div style="color:var(--text-muted); margin-top:8px;">Average season length: ${pred.avg_season_length_days} days</div>` : ''}
        `;
    } else if (pred.days_until > 0) {
        predContent.innerHTML = `
            <div style="color:var(--text-muted); margin-bottom:8px; font-size:0.95em; text-transform:uppercase; letter-spacing:1px;">Hummingbirds typically arrive around</div>
            <span class="metric-value" style="color: var(--green-bright);">${pred.predicted_display}</span>
            <div style="margin-top:12px;">
                <span style="color:#e74c3c; font-size:1.5em; font-weight:700;">${pred.days_until} days to go!</span>
            </div>
            <div style="color:var(--text-muted); margin-top:8px; font-size:0.85em;">
                Based on ${pred.based_on_years} years of data (earliest: ${pred.earliest_display}, latest: ${pred.latest_display})
            </div>
        `;
    } else {
        predContent.innerHTML = `
            <div style="color:var(--text-muted); margin-bottom:8px;">Hummingbirds typically arrive around</div>
            <span class="metric-value" style="color: var(--green-bright);">${pred.predicted_display}</span>
            <div style="color:var(--text-muted); margin-top:6px; font-size:0.85em;">
                Based on ${pred.based_on_years} years of data (earliest: ${pred.earliest_display}, latest: ${pred.latest_display})
            </div>
        `;
    }

    predSection.style.display = '';
}

/**
 * Render behavior breakdown doughnut chart
 */
function renderBehaviorChart(breakdown) {
    const ctx = document.getElementById('behaviorChart');
    if (!ctx || !breakdown || !Object.keys(breakdown).length) return;

    const labels = Object.keys(breakdown).map(k => k.charAt(0).toUpperCase() + k.slice(1));
    const data = Object.values(breakdown);
    const colors = ['#5cb84c', '#d4a017', '#3498db', '#e74c3c', '#9b59b6'];

    new Chart(ctx, {
        type: 'doughnut',
        data: {
            labels,
            datasets: [{
                data,
                backgroundColor: colors.slice(0, labels.length),
                borderColor: '#1e2a3a',
                borderWidth: 2,
            }]
        },
        options: {
            responsive: true,
            plugins: {
                legend: { position: 'bottom', labels: { padding: 12 } },
                title: { display: true, text: 'Behavior', color: '#9098a8' }
            }
        }
    });
}

/**
 * Render species breakdown pie chart
 */
function renderSpeciesChart(breakdown) {
    const ctx = document.getElementById('speciesChart');
    if (!ctx || !breakdown || !Object.keys(breakdown).length) return;

    const labels = Object.keys(breakdown);
    const data = Object.values(breakdown);
    const colors = ['#5cb84c', '#d4a017', '#3498db', '#e74c3c', '#9b59b6', '#1abc9c'];

    new Chart(ctx, {
        type: 'doughnut',
        data: {
            labels,
            datasets: [{
                data,
                backgroundColor: colors.slice(0, labels.length),
                borderColor: '#1e2a3a',
                borderWidth: 2,
            }]
        },
        options: {
            responsive: true,
            plugins: {
                legend: { position: 'bottom', labels: { padding: 12 } },
                title: { display: true, text: 'Species', color: '#9098a8' }
            }
        }
    });
}

/**
 * Render position heatmap on canvas (8x8 grid)
 */
function renderHeatmapGrid(heatmap) {
    const canvas = document.getElementById('heatmapCanvas');
    if (!canvas || !heatmap || !heatmap.length) return;

    const ctx = canvas.getContext('2d');
    const gridSize = heatmap.length;
    const cellW = canvas.width / gridSize;
    const cellH = canvas.height / gridSize;

    // Find max for normalization
    let maxVal = 0;
    for (const row of heatmap) {
        for (const v of row) {
            if (v > maxVal) maxVal = v;
        }
    }
    if (maxVal === 0) return;

    for (let r = 0; r < gridSize; r++) {
        for (let c = 0; c < gridSize; c++) {
            const intensity = heatmap[r][c] / maxVal;
            const alpha = Math.min(intensity * 0.9 + 0.05, 1);
            ctx.fillStyle = `rgba(92, 184, 76, ${alpha})`;
            ctx.fillRect(c * cellW, r * cellH, cellW - 1, cellH - 1);

            // Show count if > 0
            if (heatmap[r][c] > 0) {
                ctx.fillStyle = intensity > 0.5 ? '#fff' : '#9098a8';
                ctx.font = '11px monospace';
                ctx.textAlign = 'center';
                ctx.fillText(heatmap[r][c], c * cellW + cellW / 2, r * cellH + cellH / 2 + 4);
            }
        }
    }
}

/**
 * Render monthly trends bar chart
 */
function renderMonthlyChart(monthlyTotals) {
    const ctx = document.getElementById('monthlyChart');
    if (!ctx || !monthlyTotals || !monthlyTotals.length) return;

    const labels = monthlyTotals.map(m => m.month);
    const data = monthlyTotals.map(m => m.count);

    new Chart(ctx, {
        type: 'bar',
        data: {
            labels,
            datasets: [{
                label: 'Monthly Detections',
                data,
                backgroundColor: '#5cb84c',
                borderRadius: 3,
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: true,
            aspectRatio: 2.5,
            plugins: { legend: { display: false } },
            scales: {
                y: { beginAtZero: true, ticks: { precision: 0 }, grid: { color: 'rgba(42, 54, 72, 0.5)' } },
                x: { grid: { display: false } }
            }
        }
    });
}

/**
 * Render weather correlation horizontal bar chart
 */
function renderWeatherCorrelationChart(correlations) {
    const ctx = document.getElementById('weatherCorrelationChart');
    if (!ctx || !correlations || !Object.keys(correlations).length) return;

    const labels = Object.keys(correlations).map(k => k.charAt(0).toUpperCase() + k.slice(1));
    const data = Object.values(correlations).map(v => v || 0);
    const colors = data.map(v => v >= 0 ? '#5cb84c' : '#e74c3c');

    new Chart(ctx, {
        type: 'bar',
        data: {
            labels,
            datasets: [{
                label: 'Correlation',
                data,
                backgroundColor: colors,
                borderRadius: 3,
            }]
        },
        options: {
            indexAxis: 'y',
            responsive: true,
            maintainAspectRatio: true,
            aspectRatio: 2,
            plugins: {
                legend: { display: false },
                tooltip: {
                    callbacks: {
                        label: (item) => {
                            const v = item.raw;
                            return `${v > 0 ? '+' : ''}${v.toFixed(2)} correlation`;
                        }
                    }
                }
            },
            scales: {
                x: {
                    min: -1, max: 1,
                    grid: { color: 'rgba(42, 54, 72, 0.5)' },
                    ticks: { callback: (v) => v.toFixed(1) }
                },
                y: { grid: { display: false } }
            }
        }
    });
}

/**
 * Populate visit pattern cards
 */
function populateVisitPatterns(data) {
    const stats = data.visit_stats;
    if (!stats) return;

    const el = (id) => document.getElementById(id);
    if (stats.avg_birds_per_visit) el('avgBirds').textContent = stats.avg_birds_per_visit;
    if (stats.max_simultaneous) el('maxSimultaneous').textContent = stats.max_simultaneous;
    if (stats.avg_visit_duration_sec) el('avgDuration').textContent = stats.avg_visit_duration_sec + 's';
    if (data.sunrise_offset_avg_min != null) {
        el('sunriseOffset').textContent = data.sunrise_offset_avg_min + ' min';
    }
}

/**
 * Populate activity streaks
 */
function populateStreaks(streaks) {
    if (!streaks) return;

    const el = (id) => document.getElementById(id);
    el('currentStreak').textContent = streaks.current_streak || 0;
    el('longestStreak').textContent = streaks.longest_streak || 0;
    if (streaks.longest_start && streaks.longest_end) {
        el('longestStreakDates').textContent = `${streaks.longest_start} to ${streaks.longest_end}`;
    }
}

/**
 * Populate year-over-year comparison
 */
function populateYoY(yoy) {
    if (!yoy) return;

    const el = (id) => document.getElementById(id);
    el('yoyThisWeek').textContent = yoy.this_week || 0;
    el('yoyLastYear').textContent = yoy.last_year_same_week || 0;
}

/**
 * Populate sprinkler effect stats
 */
function populateSprinklerEffect(sprinkler) {
    if (!sprinkler) return;
    const el = (id) => document.getElementById(id);
    el('sprinklerEvents').textContent = sprinkler.events;
    el('sprinklerBefore').textContent = sprinkler.avg_before;
    el('sprinklerAfter').textContent = sprinkler.avg_after;
    const change = sprinkler.change_pct || 0;
    const sign = change > 0 ? '+' : '';
    el('sprinklerChange').textContent = `${sign}${change}%`;
    el('sprinklerChange').style.color = change > 0 ? '#5cb84c' : change < 0 ? '#e74c3c' : '#9098a8';
}

/**
 * Populate feeder management stats
 */
function populateFeederStats(feeder) {
    if (!feeder) return;
    const el = (id) => document.getElementById(id);
    el('feederCount').textContent = feeder.feeder_count || 0;
    el('daysSinceRefill').textContent = feeder.days_since_refill != null ? feeder.days_since_refill : '--';
    el('nectarProduced').textContent = feeder.nectar_produced_oz || 0;
    el('estConsumption').textContent = feeder.estimated_consumption_oz || 0;
}

/**
 * Populate prediction accuracy
 */
function populatePredictionAccuracy(accuracy) {
    if (!accuracy || !Object.keys(accuracy).length) return;
    const grid = document.getElementById('predictionGrid');
    grid.innerHTML = '';
    for (const [type, stats] of Object.entries(accuracy)) {
        const card = document.createElement('div');
        card.className = 'summary-card';
        card.innerHTML = `
            <span class="summary-label">${type.toUpperCase().replace('_', ' ')}</span>
            <span class="summary-value">${stats.avg_error_minutes != null ? stats.avg_error_minutes + ' min' : '--'}</span>
            <span class="summary-label" style="font-size:0.75em;">${stats.predictions_logged} predictions</span>
        `;
        grid.appendChild(card);
    }
}

/**
 * Populate quiet periods table
 */
function populateQuietPeriods(periods) {
    if (!periods || !periods.length) return;
    const tbody = document.getElementById('quietTableBody');
    tbody.innerHTML = '';
    for (const p of periods) {
        const tr = document.createElement('tr');
        tr.style.borderBottom = '1px solid var(--border)';
        tr.innerHTML = `
            <td style="padding:10px; font-weight:bold; color:#d4a017;">${p.hours}h</td>
            <td style="padding:10px;">${p.weather || '--'}</td>
            <td style="padding:10px; color:var(--text-muted); font-size:0.8em;">
                ${p.start ? new Date(p.start).toLocaleDateString() : ''} -
                ${p.end ? new Date(p.end).toLocaleDateString() : ''}
            </td>
        `;
        tbody.appendChild(tr);
    }
}

/**
 * Initialize stats page
 */
document.addEventListener('DOMContentLoaded', async () => {
    if (!siteData) {
        await loadSiteData();
    }
    const data = siteData;
    if (!data) return;

    setChartDefaults();
    populateStats(data);
    renderSeasonData(data);

    try {
        renderHourlyChart(data.hourly_pattern);
        renderDailyChart(data.daily_counts_30d);
    } catch (e) {
        console.warn('Chart rendering failed (Chart.js may not be loaded):', e);
    }

    // New analytics sections
    try {
        populateVisitPatterns(data);
        renderBehaviorChart(data.behavior_breakdown);
        renderSpeciesChart(data.species_breakdown);
        renderHeatmapGrid(data.position_heatmap);
        populateStreaks(data.activity_streaks);
        populateYoY(data.yoy_comparison);
        renderMonthlyChart(data.monthly_totals);
        renderWeatherCorrelationChart(data.weather_correlations);
        populateSprinklerEffect(data.sprinkler_effect);
        populateFeederStats(data.feeder_stats);
        populatePredictionAccuracy(data.prediction_accuracy);
        populateQuietPeriods(data.quiet_periods);
    } catch (e) {
        console.warn('Extended analytics rendering failed:', e);
    }
});
