/* Personal Recommendations - Web UI */

(function () {
    "use strict";

    var API_BASE = "/api";

    // State
    var currentUserId = 1;
    var currentTab = "recommendations";

    // -----------------------------------------------------------------------
    // Initialization
    // -----------------------------------------------------------------------

    function initialize() {
        loadUsers();
        checkStatus();
        setupTabs();
        setupRecommendationForm();
        setupLibraryFilters();
        setupPreferencesSave();
        setupSyncButtons();
    }

    // -----------------------------------------------------------------------
    // Users
    // -----------------------------------------------------------------------

    function loadUsers() {
        fetch(API_BASE + "/users")
            .then(function (response) { return response.json(); })
            .then(function (users) {
                var select = document.getElementById("userSelect");
                select.innerHTML = "";
                users.forEach(function (user) {
                    var option = document.createElement("option");
                    option.value = user.id;
                    option.textContent = user.display_name || user.username;
                    select.appendChild(option);
                });
                select.value = currentUserId;
                select.addEventListener("change", function () {
                    currentUserId = parseInt(select.value);
                    onTabActivated(currentTab);
                });
            })
            .catch(function () {
                // Silently ignore if users endpoint not available
            });
    }

    function checkStatus() {
        fetch(API_BASE + "/status")
            .then(function (response) { return response.json(); })
            .then(function (data) {
                var statusBar = document.getElementById("statusBar");
                if (data.status === "ready") {
                    statusBar.className = "status-bar success";
                    statusBar.textContent = "System ready";
                    setTimeout(function () { statusBar.style.display = "none"; }, 3000);
                } else {
                    statusBar.className = "status-bar loading";
                    statusBar.textContent = "System initializing...";
                }
            })
            .catch(function () {
                var statusBar = document.getElementById("statusBar");
                statusBar.className = "status-bar error";
                statusBar.textContent = "Failed to connect to server";
            });
    }

    // -----------------------------------------------------------------------
    // Tabs
    // -----------------------------------------------------------------------

    function setupTabs() {
        var buttons = document.querySelectorAll(".tab-btn");
        buttons.forEach(function (btn) {
            btn.addEventListener("click", function () {
                switchTab(btn.dataset.tab);
            });
        });
        // Activate default tab
        switchTab("recommendations");
    }

    function switchTab(name) {
        currentTab = name;
        // Update buttons
        document.querySelectorAll(".tab-btn").forEach(function (btn) {
            btn.classList.toggle("active", btn.dataset.tab === name);
        });
        // Update panels
        document.querySelectorAll(".tab-panel").forEach(function (panel) {
            panel.classList.toggle("active", panel.id === "panel-" + name);
        });
        onTabActivated(name);
    }

    function onTabActivated(name) {
        if (name === "library") {
            loadLibrary();
        } else if (name === "preferences") {
            loadPreferences();
        }
    }

    // -----------------------------------------------------------------------
    // Recommendations Tab
    // -----------------------------------------------------------------------

    function setupRecommendationForm() {
        var form = document.getElementById("recForm");
        form.addEventListener("submit", function (event) {
            event.preventDefault();
            fetchRecommendations();
        });
    }

    function fetchRecommendations() {
        var contentType = document.getElementById("recType").value;
        var count = document.getElementById("recCount").value;
        var useLlm = document.getElementById("recUseLlm").checked;
        var resultsDiv = document.getElementById("recResults");

        resultsDiv.innerHTML = '<div class="empty-state"><span class="spinner"></span> Loading recommendations...</div>';

        var params = new URLSearchParams({
            type: contentType,
            count: count,
            use_llm: useLlm.toString(),
            user_id: currentUserId.toString()
        });

        fetch(API_BASE + "/recommendations?" + params)
            .then(function (response) {
                if (!response.ok) throw new Error("HTTP " + response.status);
                return response.json();
            })
            .then(function (recs) {
                renderRecommendations(recs);
            })
            .catch(function (error) {
                resultsDiv.innerHTML = '<div class="status-bar error" style="display:block">Failed to load recommendations: ' + escapeHtml(error.message) + '</div>';
            });
    }

    function renderRecommendations(recs) {
        var resultsDiv = document.getElementById("recResults");

        if (!recs || recs.length === 0) {
            resultsDiv.innerHTML = '<div class="empty-state"><p>No recommendations available. Try adding more content to your library.</p></div>';
            return;
        }

        var html = "";
        recs.forEach(function (rec, index) {
            var hasLlmReasoning = rec.llm_reasoning && rec.llm_reasoning.trim();
            var hasBreakdown = rec.score_breakdown && Object.keys(rec.score_breakdown).length > 0;
            var defaultOpen = !hasLlmReasoning;

            html += '<div class="rec-card">';
            html += '<div class="rec-header">';
            html += '<div>';
            html += '<div class="rec-title">' + (index + 1) + '. ' + escapeHtml(rec.title) + '</div>';
            if (rec.author) {
                html += '<div class="rec-author">by ' + escapeHtml(rec.author) + '</div>';
            }
            html += '</div>';
            html += '<span class="score-badge">Score: ' + rec.score.toFixed(2) + '</span>';
            html += '</div>';

            // LLM reasoning (main position when available)
            if (hasLlmReasoning) {
                html += '<div class="rec-llm-reasoning">' + escapeHtml(rec.llm_reasoning) + '</div>';
            }

            // Regular reasoning
            if (rec.reasoning) {
                html += '<div class="rec-reasoning">' + escapeHtml(rec.reasoning) + '</div>';
            }

            // Score breakdown
            if (hasBreakdown) {
                html += '<details class="score-details"' + (defaultOpen ? ' open' : '') + '>';
                html += '<summary>Score Details</summary>';
                html += '<div class="score-breakdown">';
                var keys = Object.keys(rec.score_breakdown).sort();
                keys.forEach(function (key) {
                    var value = rec.score_breakdown[key];
                    var percent = Math.round(value * 100);
                    html += '<div class="score-row">';
                    html += '<span class="score-label">' + formatScorerName(key) + '</span>';
                    html += '<div class="score-bar-bg"><div class="score-bar-fill" style="width:' + percent + '%"></div></div>';
                    html += '<span class="score-value">' + value.toFixed(2) + '</span>';
                    html += '</div>';
                });
                html += '</div></details>';
            }

            html += '</div>';
        });

        resultsDiv.innerHTML = html;
    }

    function formatScorerName(key) {
        return key.replace(/_/g, " ").replace(/\b\w/g, function (c) { return c.toUpperCase(); });
    }

    // -----------------------------------------------------------------------
    // Library Tab
    // -----------------------------------------------------------------------

    function setupLibraryFilters() {
        var typeFilter = document.getElementById("libType");
        var statusFilter = document.getElementById("libStatus");
        typeFilter.addEventListener("change", loadLibrary);
        statusFilter.addEventListener("change", loadLibrary);
    }

    function loadLibrary() {
        var typeFilter = document.getElementById("libType").value;
        var statusFilter = document.getElementById("libStatus").value;
        var container = document.getElementById("libraryResults");

        container.innerHTML = '<div class="empty-state"><span class="spinner"></span> Loading library...</div>';

        var params = new URLSearchParams({
            user_id: currentUserId.toString(),
            limit: "100"
        });
        if (typeFilter) params.set("type", typeFilter);
        if (statusFilter) params.set("status", statusFilter);

        fetch(API_BASE + "/items?" + params)
            .then(function (response) {
                if (!response.ok) throw new Error("HTTP " + response.status);
                return response.json();
            })
            .then(function (items) {
                renderLibrary(items);
            })
            .catch(function (error) {
                container.innerHTML = '<div class="status-bar error" style="display:block">Failed to load library: ' + escapeHtml(error.message) + '</div>';
            });
    }

    function renderLibrary(items) {
        var container = document.getElementById("libraryResults");

        if (!items || items.length === 0) {
            container.innerHTML = '<div class="empty-state"><p>No items found. Try syncing your sources.</p></div>';
            return;
        }

        var html = '<div class="library-grid">';
        items.forEach(function (item) {
            html += '<div class="library-item">';
            html += '<h3>' + escapeHtml(item.title) + '</h3>';
            if (item.author) {
                html += '<div class="item-author">' + escapeHtml(item.author) + '</div>';
            }
            html += '<div>';
            html += '<span class="badge badge-type">' + formatContentType(item.content_type) + '</span>';
            html += '<span class="badge badge-status ' + item.status + '">' + formatStatus(item.status) + '</span>';
            if (item.rating) {
                html += '<span class="badge badge-rating">' + renderStars(item.rating) + '</span>';
            }
            html += '</div>';
            html += '</div>';
        });
        html += '</div>';

        container.innerHTML = html;
    }

    function formatContentType(type) {
        var map = {
            book: "Book",
            movie: "Movie",
            tv_show: "TV Show",
            video_game: "Video Game"
        };
        return map[type] || type;
    }

    function formatStatus(status) {
        var map = {
            unread: "Unread",
            currently_consuming: "In Progress",
            completed: "Completed"
        };
        return map[status] || status;
    }

    function renderStars(rating) {
        var stars = "";
        for (var i = 0; i < rating; i++) stars += "\u2605";
        return stars;
    }

    // -----------------------------------------------------------------------
    // Preferences Tab
    // -----------------------------------------------------------------------

    var scorerKeys = [
        "genre_match",
        "creator_match",
        "tag_overlap",
        "series_order",
        "rating_pattern",
        "semantic_similarity"
    ];

    function loadPreferences() {
        fetch(API_BASE + "/users/" + currentUserId + "/preferences")
            .then(function (response) { return response.json(); })
            .then(function (prefs) {
                renderPreferences(prefs);
            })
            .catch(function () {
                renderPreferences({
                    scorer_weights: {},
                    series_in_order: true,
                    variety_after_completion: false
                });
            });
    }

    var contentTypes = ["book", "movie", "tv_show", "video_game"];
    var lengthOptions = ["any", "short", "medium", "long"];

    function renderPreferences(prefs) {
        var container = document.getElementById("prefContent");

        var html = '<div class="pref-section">';
        html += '<h3>Scorer Weights</h3>';

        var defaultWeights = {
            genre_match: 2.0,
            creator_match: 1.5,
            tag_overlap: 1.0,
            series_order: 1.5,
            rating_pattern: 1.0,
            semantic_similarity: 1.5
        };

        scorerKeys.forEach(function (key) {
            var value = prefs.scorer_weights[key] !== undefined
                ? prefs.scorer_weights[key]
                : defaultWeights[key];
            html += '<div class="slider-row">';
            html += '<span class="slider-label">' + formatScorerName(key) + '</span>';
            html += '<input type="range" min="0" max="5" step="0.1" value="' + value + '" data-scorer="' + key + '" class="pref-slider">';
            html += '<span class="slider-value" data-value-for="' + key + '">' + value.toFixed(1) + '</span>';
            html += '</div>';
        });

        html += '</div>';

        html += '<div class="pref-section">';
        html += '<h3>Toggles</h3>';
        html += '<div class="toggle-row">';
        html += '<input type="checkbox" id="prefSeriesOrder"' + (prefs.series_in_order ? ' checked' : '') + '>';
        html += '<label for="prefSeriesOrder">Recommend series in order</label>';
        html += '</div>';
        html += '<div class="toggle-row">';
        html += '<input type="checkbox" id="prefVariety"' + (prefs.variety_after_completion ? ' checked' : '') + '>';
        html += '<label for="prefVariety">Variety after completion</label>';
        html += '</div>';
        html += '</div>';

        // Length preferences section
        html += '<div class="pref-section">';
        html += '<h3>Length Preferences</h3>';
        html += '<p class="help-text">Prefer short, medium, or long content per type.</p>';
        var lengthPrefs = prefs.content_length_preferences || {};
        contentTypes.forEach(function (type) {
            var value = lengthPrefs[type] || "any";
            html += '<div class="dropdown-row">';
            html += '<span class="dropdown-label">' + formatContentType(type) + '</span>';
            html += '<select class="length-select" data-content-type="' + type + '">';
            lengthOptions.forEach(function (opt) {
                html += '<option value="' + opt + '"' + (value === opt ? ' selected' : '') + '>';
                html += opt.charAt(0).toUpperCase() + opt.slice(1);
                html += '</option>';
            });
            html += '</select>';
            html += '</div>';
        });
        html += '</div>';

        // Custom rules section
        html += '<div class="pref-section">';
        html += '<h3>Custom Rules</h3>';
        html += '<p class="help-text">Natural language rules like "avoid horror" or "prefer sci-fi".</p>';
        html += '<div id="customRulesList">';
        var customRules = prefs.custom_rules || [];
        if (customRules.length === 0) {
            html += '<div class="empty-rules">No custom rules defined</div>';
        } else {
            customRules.forEach(function (rule, index) {
                html += '<div class="rule-item">';
                html += '<span class="rule-text">' + escapeHtml(rule) + '</span>';
                html += '<button class="btn btn-small btn-danger remove-rule-btn" data-index="' + index + '">Remove</button>';
                html += '</div>';
            });
        }
        html += '</div>';
        html += '<div class="add-rule-form">';
        html += '<input type="text" id="newRuleInput" placeholder="e.g., avoid horror, prefer sci-fi">';
        html += '<button class="btn btn-small" id="addRuleBtn">Add Rule</button>';
        html += '</div>';
        html += '</div>';

        html += '<button class="btn btn-primary" id="prefSaveBtn">Save Preferences</button>';
        html += ' <span id="prefSaveStatus"></span>';

        container.innerHTML = html;

        // Attach slider listeners
        container.querySelectorAll(".pref-slider").forEach(function (slider) {
            slider.addEventListener("input", function () {
                var label = container.querySelector('[data-value-for="' + slider.dataset.scorer + '"]');
                if (label) label.textContent = parseFloat(slider.value).toFixed(1);
            });
        });

        // Attach remove rule listeners
        container.querySelectorAll(".remove-rule-btn").forEach(function (btn) {
            btn.addEventListener("click", function () {
                removeCustomRule(parseInt(btn.dataset.index));
            });
        });

        // Attach add rule listener
        document.getElementById("addRuleBtn").addEventListener("click", addCustomRule);
        document.getElementById("newRuleInput").addEventListener("keypress", function (e) {
            if (e.key === "Enter") addCustomRule();
        });

        // Attach save listener
        document.getElementById("prefSaveBtn").addEventListener("click", savePreferences);
    }

    // Custom rules state (loaded from prefs, modified locally, saved together)
    var currentCustomRules = [];

    function removeCustomRule(index) {
        // Get current rules from DOM
        var rules = [];
        document.querySelectorAll("#customRulesList .rule-text").forEach(function (el) {
            rules.push(el.textContent);
        });
        rules.splice(index, 1);
        currentCustomRules = rules;
        // Re-render just the rules list
        renderCustomRulesList(rules);
    }

    function addCustomRule() {
        var input = document.getElementById("newRuleInput");
        var rule = input.value.trim();
        if (!rule) return;

        // Get current rules from DOM
        var rules = [];
        document.querySelectorAll("#customRulesList .rule-text").forEach(function (el) {
            rules.push(el.textContent);
        });
        rules.push(rule);
        currentCustomRules = rules;

        input.value = "";
        renderCustomRulesList(rules);
    }

    function renderCustomRulesList(rules) {
        var container = document.getElementById("customRulesList");
        if (!rules || rules.length === 0) {
            container.innerHTML = '<div class="empty-rules">No custom rules defined</div>';
            return;
        }

        var html = "";
        rules.forEach(function (rule, index) {
            html += '<div class="rule-item">';
            html += '<span class="rule-text">' + escapeHtml(rule) + '</span>';
            html += '<button class="btn btn-small btn-danger remove-rule-btn" data-index="' + index + '">Remove</button>';
            html += '</div>';
        });
        container.innerHTML = html;

        // Re-attach listeners
        container.querySelectorAll(".remove-rule-btn").forEach(function (btn) {
            btn.addEventListener("click", function () {
                removeCustomRule(parseInt(btn.dataset.index));
            });
        });
    }

    function savePreferences() {
        var weights = {};
        document.querySelectorAll(".pref-slider").forEach(function (slider) {
            weights[slider.dataset.scorer] = parseFloat(slider.value);
        });

        // Collect length preferences
        var lengthPrefs = {};
        document.querySelectorAll(".length-select").forEach(function (select) {
            lengthPrefs[select.dataset.contentType] = select.value;
        });

        // Collect custom rules from DOM
        var customRules = [];
        document.querySelectorAll("#customRulesList .rule-text").forEach(function (el) {
            customRules.push(el.textContent);
        });

        var payload = {
            scorer_weights: weights,
            series_in_order: document.getElementById("prefSeriesOrder").checked,
            variety_after_completion: document.getElementById("prefVariety").checked,
            content_length_preferences: lengthPrefs,
            custom_rules: customRules
        };

        var statusSpan = document.getElementById("prefSaveStatus");
        statusSpan.textContent = "Saving...";

        fetch(API_BASE + "/users/" + currentUserId + "/preferences", {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload)
        })
            .then(function (response) {
                if (!response.ok) throw new Error("HTTP " + response.status);
                return response.json();
            })
            .then(function () {
                statusSpan.textContent = "Saved!";
                setTimeout(function () { statusSpan.textContent = ""; }, 2000);
            })
            .catch(function (error) {
                statusSpan.textContent = "Error: " + error.message;
            });
    }

    function setupPreferencesSave() {
        // Handled dynamically in renderPreferences
    }

    // -----------------------------------------------------------------------
    // Sync Tab
    // -----------------------------------------------------------------------

    function setupSyncButtons() {
        document.querySelectorAll(".sync-btn").forEach(function (btn) {
            btn.addEventListener("click", function () {
                triggerSync(btn.dataset.source, btn);
            });
        });
    }

    function triggerSync(source, btn) {
        var resultDiv = btn.parentElement.querySelector(".sync-result");
        btn.disabled = true;
        btn.textContent = "Syncing...";
        resultDiv.innerHTML = '<span class="spinner"></span> Running...';

        fetch(API_BASE + "/update", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ source: source })
        })
            .then(function (response) {
                if (!response.ok) return response.json().then(function (d) { throw new Error(d.detail || "HTTP " + response.status); });
                return response.json();
            })
            .then(function (data) {
                resultDiv.textContent = data.message || ("Updated " + data.count + " items");
                resultDiv.style.color = "#2e7d32";
            })
            .catch(function (error) {
                resultDiv.textContent = "Error: " + error.message;
                resultDiv.style.color = "#c62828";
            })
            .finally(function () {
                btn.disabled = false;
                btn.textContent = "Sync " + formatSourceName(source);
            });
    }

    function formatSourceName(source) {
        if (source === "all") return "All Sources";
        return source.charAt(0).toUpperCase() + source.slice(1);
    }

    // -----------------------------------------------------------------------
    // Helpers
    // -----------------------------------------------------------------------

    function escapeHtml(text) {
        if (!text) return "";
        var div = document.createElement("div");
        div.textContent = text;
        return div.innerHTML;
    }

    // -----------------------------------------------------------------------
    // Boot
    // -----------------------------------------------------------------------

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", initialize);
    } else {
        initialize();
    }
})();
