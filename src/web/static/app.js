/* Personal Recommendations - Web UI */

(function () {
    "use strict";

    var API_BASE = "/api";

    // Configure marked for chat markdown rendering
    if (typeof marked !== "undefined") {
        marked.setOptions({ breaks: true, gfm: true });
    }

    // State
    var currentUserId = 1;
    var currentTab = "recommendations";
    var aiFeatures = {
        ai_enabled: false,
        embeddings_enabled: false,
        llm_reasoning_enabled: false
    };

    // Library pagination state
    var libraryState = {
        offset: 0,
        limit: 50,
        loading: false,
        hasMore: true,
        items: []
    };

    // -----------------------------------------------------------------------
    // Initialization
    // -----------------------------------------------------------------------

    function initialize() {
        applyStoredTheme();
        loadUsers();
        checkStatus();
        setupTabs();
        setupMobileSidebar();
        setupRecommendationForm();
        setupLibraryFilters();
        setupPreferencesSave();
        setupSyncButtons();
        setupChat();
        loadThemes();
    }

    // -----------------------------------------------------------------------
    // Themes
    // -----------------------------------------------------------------------

    function applyStoredTheme() {
        var themeId = localStorage.getItem("theme");
        if (themeId) {
            applyTheme(themeId);
        }
    }

    function applyTheme(themeId) {
        var link = document.getElementById("theme-stylesheet");
        if (!link) return;
        if (themeId === "nord") {
            // Nord is the default in :root, clear the override
            link.href = "/static/themes/nord/colors.css";
        } else {
            link.href = "/static/themes/" + themeId + "/colors.css";
        }
        localStorage.setItem("theme", themeId);
    }

    function loadThemes() {
        var select = document.getElementById("themeSelect");
        if (!select) return;

        // Fetch themes and default in parallel
        Promise.all([
            fetch(API_BASE + "/themes").then(function (response) { return response.json(); }),
            fetch(API_BASE + "/themes/default").then(function (response) { return response.json(); })
        ])
            .then(function (results) {
                var themes = results[0];
                var defaultData = results[1];

                if (!themes || themes.length === 0) return;

                select.innerHTML = "";
                themes.forEach(function (theme) {
                    var option = document.createElement("option");
                    option.value = theme.id;
                    option.textContent = theme.name;
                    select.appendChild(option);
                });

                // Use localStorage preference if set, otherwise config default
                var storedTheme = localStorage.getItem("theme");
                var activeTheme = storedTheme || defaultData.theme || "nord";
                select.value = activeTheme;

                // Apply theme if not already applied from localStorage
                if (!storedTheme && defaultData.theme) {
                    applyTheme(defaultData.theme);
                }

                select.addEventListener("change", function () {
                    applyTheme(select.value);
                });
            })
            .catch(function () {
                // Silently ignore if themes endpoint not available
            });
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

                // Store feature flags
                if (data.features) {
                    aiFeatures = data.features;
                }

                // Hide AI-dependent UI elements when disabled
                updateAiReasoningVisibility();
                updateChatTabVisibility();
            })
            .catch(function () {
                var statusBar = document.getElementById("statusBar");
                statusBar.className = "status-bar error";
                statusBar.textContent = "Failed to connect to server";
            });
    }

    function updateAiReasoningVisibility() {
        var aiReasoningContainer = document.getElementById("recUseLlm");
        if (aiReasoningContainer) {
            var container = aiReasoningContainer.closest(".checkbox-label");
            if (container) {
                if (!aiFeatures.ai_enabled || !aiFeatures.llm_reasoning_enabled) {
                    container.style.display = "none";
                    aiReasoningContainer.checked = false;
                } else {
                    container.style.display = "";
                }
            }
        }
    }

    function updateChatTabVisibility() {
        var chatNavItem = document.querySelector('.nav-item[data-tab="chat"]');
        if (chatNavItem) {
            chatNavItem.style.display = aiFeatures.ai_enabled ? "" : "none";
        }
    }

    // -----------------------------------------------------------------------
    // Tabs (Sidebar Navigation)
    // -----------------------------------------------------------------------

    function setupTabs() {
        var buttons = document.querySelectorAll(".nav-item");
        buttons.forEach(function (btn) {
            btn.addEventListener("click", function () {
                switchTab(btn.dataset.tab);
                // Close mobile sidebar on navigation
                closeMobileSidebar();
            });
        });
        // Activate default tab
        switchTab("recommendations");
    }

    function switchTab(name) {
        currentTab = name;
        // Update nav items
        document.querySelectorAll(".nav-item").forEach(function (btn) {
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
            resetAndLoadLibrary();
        } else if (name === "preferences") {
            loadPreferences();
        } else if (name === "chat") {
            loadChatData();
        } else if (name === "data") {
            loadEnrichmentStats();
        }
    }

    // -----------------------------------------------------------------------
    // Mobile Sidebar
    // -----------------------------------------------------------------------

    function setupMobileSidebar() {
        var toggle = document.getElementById("sidebarToggle");
        var overlay = document.getElementById("sidebarOverlay");

        if (toggle) {
            toggle.addEventListener("click", function () {
                var sidebar = document.getElementById("sidebar");
                sidebar.classList.toggle("open");
                overlay.classList.toggle("visible");
            });
        }

        if (overlay) {
            overlay.addEventListener("click", closeMobileSidebar);
        }
    }

    function closeMobileSidebar() {
        var sidebar = document.getElementById("sidebar");
        var overlay = document.getElementById("sidebarOverlay");
        if (sidebar) sidebar.classList.remove("open");
        if (overlay) overlay.classList.remove("visible");
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

        // Use streaming endpoint when LLM reasoning is enabled
        if (useLlm && aiFeatures.llm_reasoning_enabled) {
            fetchRecommendationsStreaming(contentType, count, resultsDiv);
            return;
        }

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
                resultsDiv.textContent = "";
                var errDiv = document.createElement("div");
                errDiv.className = "status-bar error";
                errDiv.style.display = "block";
                errDiv.textContent = "Failed to load recommendations: " + error.message;
                resultsDiv.appendChild(errDiv);
            });
    }

    function fetchRecommendationsStreaming(contentType, count, resultsDiv) {
        var params = new URLSearchParams({
            type: contentType,
            count: count,
            user_id: currentUserId.toString()
        });

        fetch(API_BASE + "/recommendations/stream?" + params)
            .then(function (response) {
                if (!response.ok) throw new Error("HTTP " + response.status);

                // Fallback: if ReadableStream not supported, read full response
                if (!response.body || !response.body.getReader) {
                    return response.text().then(function (text) {
                        processRecSSELines(text, resultsDiv);
                    });
                }

                var reader = response.body.getReader();
                var decoder = new TextDecoder();
                var buffer = "";

                function read() {
                    return reader.read().then(function (result) {
                        if (result.done) return;
                        buffer += decoder.decode(result.value, { stream: true });

                        // Process complete lines
                        var lines = buffer.split("\n");
                        buffer = lines.pop() || "";
                        lines.forEach(function (line) {
                            processRecSSELine(line, resultsDiv);
                        });

                        return read();
                    });
                }

                return read();
            })
            .catch(function (error) {
                // Fallback to sync endpoint on streaming failure
                console.warn("Streaming failed, falling back to sync:", error);
                var fallbackParams = new URLSearchParams({
                    type: contentType,
                    count: count,
                    use_llm: "true",
                    user_id: currentUserId.toString()
                });
                fetch(API_BASE + "/recommendations?" + fallbackParams)
                    .then(function (r) {
                        if (!r.ok) throw new Error("HTTP " + r.status);
                        return r.json();
                    })
                    .then(function (recs) { renderRecommendations(recs); })
                    .catch(function (err) {
                        resultsDiv.textContent = "";
                        var errDiv = document.createElement("div");
                        errDiv.className = "status-bar error";
                        errDiv.style.display = "block";
                        errDiv.textContent = "Failed to load recommendations: " + err.message;
                        resultsDiv.appendChild(errDiv);
                    });
            });
    }

    function processRecSSELines(text, resultsDiv) {
        text.split("\n").forEach(function (line) {
            processRecSSELine(line, resultsDiv);
        });
    }

    function processRecSSELine(line, resultsDiv) {
        if (!line.startsWith("data: ")) return;
        var data;
        try {
            data = JSON.parse(line.substring(6));
        } catch (e) {
            return;
        }

        if (data.type === "recommendations") {
            renderRecommendations(data.items, true);
        } else if (data.type === "blurb") {
            var idx = data.index;
            if (typeof idx !== "number" || idx < 0) return;
            var cards = resultsDiv.querySelectorAll(".rec-card");
            if (idx >= cards.length) return;
            var card = cards[idx];
            var dots = card.querySelector(".loading-dots");
            if (dots) {
                var reasoningDiv = document.createElement("div");
                reasoningDiv.className = "rec-llm-reasoning";
                reasoningDiv.innerHTML = renderMarkdown(data.llm_reasoning);
                dots.parentNode.replaceChild(reasoningDiv, dots);
            }
            // Fold pipeline reasoning into score details now that LLM reasoning exists
            var pipelineReasoning = card.querySelector(".rec-reasoning:not(.rec-reasoning-folded)");
            if (pipelineReasoning) {
                var details = card.querySelector(".score-details");
                if (details) {
                    var folded = pipelineReasoning.cloneNode(true);
                    folded.classList.add("rec-reasoning-folded");
                    details.insertBefore(folded, details.querySelector(".score-breakdown"));
                }
                pipelineReasoning.remove();
            }
        } else if (data.type === "done") {
            // Remove any remaining loading dots (blurb generation failed for those)
            resultsDiv.querySelectorAll(".loading-dots").forEach(function (el) {
                el.remove();
            });
        } else if (data.type === "error") {
            resultsDiv.textContent = "";
            var errDiv = document.createElement("div");
            errDiv.className = "status-bar error";
            errDiv.style.display = "block";
            errDiv.textContent = "Failed to load recommendations: " + (data.message || "Unknown error");
            resultsDiv.appendChild(errDiv);
        }
    }

    function renderRecommendations(recs, streaming) {
        var resultsDiv = document.getElementById("recResults");

        if (!recs || recs.length === 0) {
            resultsDiv.textContent = "";
            var empty = document.createElement("div");
            empty.className = "empty-state";
            var p = document.createElement("p");
            p.textContent = "No recommendations available. Try adding more content to your library.";
            empty.appendChild(p);
            resultsDiv.appendChild(empty);
            return;
        }

        var html = "";
        recs.forEach(function (rec, index) {
            var hasLlmReasoning = rec.llm_reasoning && rec.llm_reasoning.trim();
            var hasBreakdown = rec.score_breakdown && Object.keys(rec.score_breakdown).length > 0;
            var defaultOpen = !hasLlmReasoning && !streaming;

            html += '<div class="rec-card">';
            html += '<div class="rec-header">';
            html += '<div>';
            html += '<div class="rec-title">';
            html += '<span class="rec-rank">' + (index + 1) + '.</span> ';
            html += escapeHtml(rec.title);
            html += '</div>';
            if (rec.author) {
                html += '<div class="rec-author">by ' + escapeHtml(rec.author) + '</div>';
            }
            html += '</div>';
            html += '<div class="rec-actions">';
            html += '<span class="badge badge-score">' + rec.score.toFixed(2) + '</span>';
            if (rec.db_id) {
                html += '<button class="btn btn-small btn-ignore ignore-rec-btn" data-db-id="' + rec.db_id + '" title="Ignore this item">Ignore</button>';
            }
            html += '</div>';
            html += '</div>';

            // LLM reasoning (rendered as markdown when available)
            if (hasLlmReasoning) {
                html += '<div class="rec-llm-reasoning">' + renderMarkdown(rec.llm_reasoning) + '</div>';
            } else if (streaming) {
                // Show loading dots placeholder while blurbs stream in
                html += '<div class="loading-dots"><span></span><span></span><span></span></div>';
            }

            // When LLM reasoning is active, fold pipeline reasoning into score details.
            // When LLM reasoning is absent, show pipeline reasoning in its normal position.
            if (!hasLlmReasoning && !streaming && rec.reasoning) {
                html += '<div class="rec-reasoning">' + escapeHtml(rec.reasoning) + '</div>';
            }

            // Score breakdown (includes pipeline reasoning when LLM reasoning is active)
            if (hasBreakdown || (hasLlmReasoning && rec.reasoning)) {
                html += '<details class="score-details"' + (defaultOpen ? ' open' : '') + '>';
                html += '<summary>Score Details</summary>';

                // Pipeline reasoning folded in when LLM reasoning is shown above
                if (hasLlmReasoning && rec.reasoning) {
                    html += '<div class="rec-reasoning rec-reasoning-folded">' + escapeHtml(rec.reasoning) + '</div>';
                }

                if (hasBreakdown) {
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
                    html += '</div>';
                }
                html += '</details>';
            }

            html += '</div>';
        });

        resultsDiv.innerHTML = html;

        // Attach ignore button listeners
        resultsDiv.querySelectorAll(".ignore-rec-btn").forEach(function (btn) {
            btn.addEventListener("click", function () {
                ignoreItem(parseInt(btn.dataset.dbId), true, btn);
            });
        });
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
        var showIgnoredCheckbox = document.getElementById("libShowIgnored");
        var exportBtn = document.getElementById("exportBtn");

        typeFilter.addEventListener("change", function() {
            updateStatusFilterLabels();
            updateExportButton();
            resetAndLoadLibrary();
        });
        statusFilter.addEventListener("change", resetAndLoadLibrary);
        showIgnoredCheckbox.addEventListener("change", resetAndLoadLibrary);

        if (exportBtn) {
            exportBtn.addEventListener("click", exportLibrary);
        }

        // Setup infinite scroll
        window.addEventListener("scroll", handleLibraryScroll);

        // Initialize status labels based on default type selection
        updateStatusFilterLabels();
        updateExportButton();
    }

    function updateExportButton() {
        var typeFilter = document.getElementById("libType");
        var exportBtn = document.getElementById("exportBtn");
        if (!exportBtn) return;

        var hasType = typeFilter.value !== "";
        exportBtn.disabled = !hasType;
        exportBtn.title = hasType ? "Export library items" : "Select a content type to export";
    }

    function exportLibrary() {
        var typeFilter = document.getElementById("libType").value;
        var formatSelect = document.getElementById("exportFormat").value;
        if (!typeFilter) return;

        var params = new URLSearchParams({
            type: typeFilter,
            format: formatSelect,
            user_id: currentUserId.toString()
        });

        window.location.href = API_BASE + "/items/export?" + params;
    }

    function updateStatusFilterLabels() {
        // Update the "unread" option label based on selected content type
        var typeFilter = document.getElementById("libType");
        var statusFilter = document.getElementById("libStatus");
        var contentType = typeFilter.value;

        var unreadOption = statusFilter.querySelector('option[value="unread"]');
        if (unreadOption) {
            var label;
            if (contentType === "book") {
                label = unreadOption.getAttribute("data-book");
            } else if (contentType === "movie") {
                label = unreadOption.getAttribute("data-movie");
            } else if (contentType === "tv_show") {
                label = unreadOption.getAttribute("data-tv_show");
            } else if (contentType === "video_game") {
                label = unreadOption.getAttribute("data-video_game");
            } else {
                label = unreadOption.getAttribute("data-default");
            }
            unreadOption.textContent = label || "Not Started";
        }
    }

    function handleLibraryScroll() {
        // Only handle scroll when on library tab
        if (currentTab !== "library") return;
        if (libraryState.loading || !libraryState.hasMore) return;

        // Check if user scrolled near bottom (within 200px)
        var scrollTop = window.pageYOffset || document.documentElement.scrollTop;
        var windowHeight = window.innerHeight;
        var docHeight = document.documentElement.scrollHeight;

        if (scrollTop + windowHeight >= docHeight - 200) {
            loadMoreLibraryItems();
        }
    }

    function resetAndLoadLibrary() {
        // Reset pagination state when filters change
        libraryState.offset = 0;
        libraryState.items = [];
        libraryState.hasMore = true;
        libraryState.loading = false;
        loadLibrary(true);
    }

    function loadLibrary(isReset) {
        if (libraryState.loading) return;

        var typeFilter = document.getElementById("libType").value;
        var statusFilter = document.getElementById("libStatus").value;
        var container = document.getElementById("libraryResults");

        libraryState.loading = true;

        if (isReset) {
            container.innerHTML = '<div class="empty-state"><span class="spinner"></span> Loading library...</div>';
        }

        var showIgnored = document.getElementById("libShowIgnored").checked;

        var params = new URLSearchParams({
            user_id: currentUserId.toString(),
            limit: libraryState.limit.toString(),
            offset: libraryState.offset.toString()
        });
        if (typeFilter) params.set("type", typeFilter);
        if (statusFilter) params.set("status", statusFilter);
        if (showIgnored) params.set("include_ignored", "true");

        fetch(API_BASE + "/items?" + params)
            .then(function (response) {
                if (!response.ok) throw new Error("HTTP " + response.status);
                return response.json();
            })
            .then(function (items) {
                libraryState.loading = false;

                if (items.length < libraryState.limit) {
                    libraryState.hasMore = false;
                }

                if (isReset) {
                    libraryState.items = items;
                } else {
                    libraryState.items = libraryState.items.concat(items);
                }

                libraryState.offset += items.length;
                renderLibrary(libraryState.items, libraryState.hasMore);
            })
            .catch(function (error) {
                libraryState.loading = false;
                if (isReset) {
                    container.innerHTML = '<div class="status-bar error" style="display:block">Failed to load library: ' + escapeHtml(error.message) + '</div>';
                }
            });
    }

    function loadMoreLibraryItems() {
        loadLibrary(false);
    }

    function renderLibrary(items, hasMore) {
        var container = document.getElementById("libraryResults");

        if (!items || items.length === 0) {
            container.innerHTML = '<div class="empty-state"><p>No items found. Try syncing your sources.</p></div>';
            return;
        }

        var html = '<div class="library-grid">';
        items.forEach(function (item) {
            var ignoredClass = item.ignored ? " ignored" : "";
            html += '<div class="library-item' + ignoredClass + '" data-db-id="' + (item.db_id || "") + '">';
            html += '<h3>' + escapeHtml(item.title) + '</h3>';
            if (item.author) {
                html += '<div class="item-author">' + escapeHtml(item.author) + '</div>';
            }
            html += '<div class="library-item-badges">';
            html += '<span class="badge badge-type">' + formatContentType(item.content_type) + '</span>';
            html += '<span class="badge badge-status ' + item.status + '">' + formatStatus(item.status, item.content_type) + '</span>';
            if (item.rating) {
                html += '<span class="badge badge-rating">' + renderStars(item.rating) + '</span>';
            }
            if (item.ignored) {
                html += '<span class="badge badge-ignored">Ignored</span>';
            }
            html += '</div>';
            if (item.db_id) {
                var ignoreLabel = item.ignored ? "Unignore" : "Ignore";
                var ignoreClass = item.ignored ? "btn-unignore" : "btn-ignore";
                html += '<div class="library-item-actions">';
                html += '<button class="btn btn-small btn-secondary edit-lib-btn" data-db-id="' + item.db_id + '" title="Edit this item">Edit</button>';
                html += '<button class="btn btn-small ' + ignoreClass + ' ignore-lib-btn" data-db-id="' + item.db_id + '" data-ignored="' + (item.ignored ? "true" : "false") + '" title="' + ignoreLabel + ' this item">' + ignoreLabel + '</button>';
                html += '</div>';
            }
            html += '</div>';
        });
        html += '</div>';

        // Add loading indicator if more items available
        if (hasMore) {
            html += '<div class="library-load-more"><span class="spinner"></span> Loading more...</div>';
        } else if (items.length > 0) {
            html += '<div class="library-end">All ' + items.length + ' items loaded</div>';
        }

        container.innerHTML = html;

        // Attach edit button listeners
        container.querySelectorAll(".edit-lib-btn").forEach(function (btn) {
            btn.addEventListener("click", function () {
                openEditModal(parseInt(btn.dataset.dbId));
            });
        });

        // Attach ignore button listeners
        container.querySelectorAll(".ignore-lib-btn").forEach(function (btn) {
            btn.addEventListener("click", function () {
                var isIgnored = btn.dataset.ignored === "true";
                ignoreItem(parseInt(btn.dataset.dbId), !isIgnored, btn);
            });
        });
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

    function formatStatus(status, contentType) {
        // "In Progress" and "Completed" work for all content types
        if (status === "currently_consuming") {
            return "In Progress";
        }
        if (status === "completed") {
            return "Completed";
        }
        // "Unread" status has content-type-specific labels
        if (status === "unread") {
            if (contentType === "video_game") {
                return "Unplayed";
            }
            if (contentType === "movie" || contentType === "tv_show") {
                return "Unwatched";
            }
            return "Unread";  // books and default
        }
        return status;
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
            // Hide semantic_similarity if embeddings are disabled
            if (key === "semantic_similarity" && (!aiFeatures.ai_enabled || !aiFeatures.embeddings_enabled)) {
                return;
            }

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
        html += '<button class="btn btn-small btn-primary" id="addRuleBtn">Add Rule</button>';
        html += '</div>';
        html += '</div>';

        html += '<button class="btn btn-primary" id="prefSaveBtn">Save Preferences</button>';
        html += ' <span id="prefSaveStatus" class="text-muted"></span>';

        container.innerHTML = html;

        // Attach slider listeners with gradient fill
        container.querySelectorAll(".pref-slider").forEach(function (slider) {
            updateSliderFill(slider);
            slider.addEventListener("input", function () {
                var label = container.querySelector('[data-value-for="' + slider.dataset.scorer + '"]');
                if (label) label.textContent = parseFloat(slider.value).toFixed(1);
                updateSliderFill(slider);
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
        statusSpan.className = "text-muted";

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
                statusSpan.className = "text-success";
                setTimeout(function () { statusSpan.textContent = ""; }, 2000);
            })
            .catch(function (error) {
                statusSpan.textContent = "Error: " + error.message;
                statusSpan.className = "text-error";
            });
    }

    function setupPreferencesSave() {
        // Handled dynamically in renderPreferences
    }

    // -----------------------------------------------------------------------
    // Data Tab (Sync)
    // -----------------------------------------------------------------------

    var syncState = {
        polling: false,
        pollInterval: null
    };

    var enrichmentState = {
        polling: false,
        pollInterval: null
    };

    function setupSyncButtons() {
        loadSyncSources();
        checkSyncStatus();
        loadEnrichmentStats();
        checkEnrichmentStatus();
    }

    function loadSyncSources() {
        var grid = document.getElementById("syncSourcesGrid");
        if (!grid) return;

        // Reload config first to pick up any changes, then fetch sources
        fetch(API_BASE + "/config/reload", { method: "POST" })
            .catch(function () { /* ignore reload errors */ })
            .then(function () {
                // Fetch both sync sources and GOG status in parallel
                return Promise.all([
                    fetch(API_BASE + "/sync/sources").then(function (r) { return r.json(); }),
                    fetch(API_BASE + "/gog/status").then(function (r) { return r.json(); }).catch(function () { return null; })
                ]);
            })
            .then(function (results) {
                var sources = results[0];
                var gogStatus = results[1];
                renderSyncSources(grid, sources, gogStatus);
            })
            .catch(function (error) {
                grid.innerHTML = '<div class="empty-state"><span class="text-error">Failed to load sync sources: ' + escapeHtml(error.message) + '</span></div>';
            });
    }

    function renderSyncSources(container, sources, gogStatus) {
        if (!sources || sources.length === 0) {
            container.innerHTML = '<div class="empty-state"><p>No sync sources configured. Add sources to config.yaml with enabled: true.</p></div>';
            return;
        }

        // Store GOG status for use by OAuth functions
        if (gogStatus) {
            gogState.authUrl = gogStatus.auth_url;
            gogState.connected = gogStatus.connected;
        }

        var html = "";
        sources.forEach(function (source) {
            html += '<div class="sync-card" data-source-id="' + escapeHtml(source.id) + '">';
            html += '<h3>' + escapeHtml(source.display_name) + '</h3>';
            html += '<p class="sync-plugin-name">Plugin: ' + escapeHtml(source.plugin_display_name) + '</p>';

            // Special handling for GOG when not connected
            if (source.id === "gog" && gogStatus && gogStatus.enabled && !gogStatus.connected) {
                html += '<div class="gog-connect-flow" id="gogConnectFlow">';
                html += '<div class="gog-connect-step">';
                html += '<button class="btn btn-primary" onclick="window.openGogAuth()">Connect GOG Account</button>';
                html += '</div>';
                html += '<div class="gog-connect-step gog-code-step" id="gogCodeStep" style="display:none;">';
                html += '<p class="help-text" style="margin:8px 0;">Paste the redirect URL after logging in:</p>';
                html += '<div class="gog-input-row">';
                html += '<input type="text" id="gogCodeInput" placeholder="Paste URL here...">';
                html += '<button class="btn btn-primary" onclick="window.submitGogCode()">Connect</button>';
                html += '</div>';
                html += '<div id="gogConnectStatus" style="margin-top:8px;"></div>';
                html += '</div>';
                html += '</div>';
            } else {
                html += '<button class="btn btn-primary sync-btn" data-source="' + escapeHtml(source.id) + '" data-display-name="' + escapeHtml(source.display_name) + '">Sync</button>';
            }

            html += '</div>';
        });

        if (sources.length > 1) {
            html += '<div class="sync-card">';
            html += '<h3>All Sources</h3>';
            html += '<p class="sync-plugin-name">Sync all enabled sources at once</p>';
            html += '<button class="btn btn-secondary sync-btn" data-source="all" data-display-name="All Sources">Sync All Sources</button>';
            html += '</div>';
        }

        container.innerHTML = html;

        container.querySelectorAll(".sync-btn").forEach(function (btn) {
            btn.addEventListener("click", function () {
                triggerSync(btn.dataset.source);
            });
        });
    }

    function triggerSync(source) {
        // Disable all sync buttons
        setSyncButtonsDisabled(true, "Starting...");
        updateSyncStatus("Starting sync for " + formatSourceName(source) + "...", "info");

        fetch(API_BASE + "/update", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ source: source })
        })
            .then(function (response) {
                if (!response.ok) {
                    return response.json().then(function (d) {
                        throw new Error(d.detail || "HTTP " + response.status);
                    });
                }
                return response.json();
            })
            .then(function (data) {
                updateSyncStatus(data.message, "info");
                // Start polling for progress
                startSyncPolling();
            })
            .catch(function (error) {
                updateSyncStatus("Error: " + error.message, "error");
                setSyncButtonsDisabled(false);
            });
    }

    function startSyncPolling() {
        if (syncState.polling) return;
        syncState.polling = true;
        syncState.pollInterval = setInterval(checkSyncStatus, 2000);
    }

    function stopSyncPolling() {
        syncState.polling = false;
        if (syncState.pollInterval) {
            clearInterval(syncState.pollInterval);
            syncState.pollInterval = null;
        }
    }

    function checkSyncStatus() {
        fetch(API_BASE + "/sync/status")
            .then(function (response) { return response.json(); })
            .then(function (data) {
                var status = data.status;
                var job = data.job;

                if (status === "running" && job) {
                    // Sync is running - update UI
                    setSyncButtonsDisabled(true, "Syncing...");
                    var message = buildProgressMessage(job);
                    updateSyncStatus(message, "info", job);

                    // Start polling if not already
                    if (!syncState.polling) {
                        startSyncPolling();
                    }
                } else if (status === "completed" && job) {
                    // Sync completed
                    stopSyncPolling();
                    setSyncButtonsDisabled(false);
                    var completedMsg = "Completed: " + job.items_processed + " items synced";
                    if (job.error_count > 0) {
                        completedMsg += " (" + job.error_count + " errors)";
                    }
                    updateSyncStatus(completedMsg, "success", null);
                    // Refresh enrichment stats after sync
                    loadEnrichmentStats();
                    checkEnrichmentStatus();
                } else if (status === "failed" && job) {
                    // Sync failed
                    stopSyncPolling();
                    setSyncButtonsDisabled(false);
                    updateSyncStatus("Failed: " + (job.error_message || "Unknown error"), "error", null);
                } else {
                    // Idle - no sync running
                    stopSyncPolling();
                    setSyncButtonsDisabled(false);
                    updateSyncStatus("", "", null);
                }
            })
            .catch(function (error) {
                console.error("Error checking sync status:", error);
            });
    }

    function buildProgressMessage(job) {
        var parts = [];

        // Lead with progress count when we have it (e.g. "20/133")
        if (job.total_items != null && job.total_items > 0) {
            parts.push(job.items_processed + "/" + job.total_items);
            if (job.progress_percent != null) {
                parts.push("(" + job.progress_percent + "%)");
            }
        } else if (job.items_processed > 0) {
            parts.push(job.items_processed + " items so far");
        }

        // Show only the currently active source, not the full "sync all" list
        parts.push("—");
        var displaySource = job.current_source || job.source;
        parts.push("Syncing " + displaySource);

        // Current activity (e.g. "Fetching game details" or current item name)
        if (job.current_item) {
            parts.push(":");
            parts.push(truncate(job.current_item, 50));
        } else {
            parts.push("...");
        }

        return parts.join(" ");
    }

    function truncate(str, maxLen) {
        if (!str || str.length <= maxLen) return str;
        return str.substring(0, maxLen - 3) + "...";
    }

    function setSyncButtonsDisabled(disabled, buttonText) {
        document.querySelectorAll(".sync-btn").forEach(function (btn) {
            btn.disabled = disabled;
            if (buttonText && disabled) {
                btn.textContent = buttonText;
            } else if (!disabled) {
                var displayName = btn.dataset.displayName || formatSourceName(btn.dataset.source);
                btn.textContent = "Sync " + displayName;
            }
        });
    }

    function updateSyncStatus(message, type, job) {
        var container = document.getElementById("syncStatusContainer");
        var statusDiv = document.getElementById("syncStatusMessage");
        var progressBar = document.getElementById("syncProgressBar");
        if (!container || !statusDiv) return;

        statusDiv.textContent = message;
        container.style.display = message ? "block" : "none";
        statusDiv.className = "sync-status-message";

        if (type === "error") {
            statusDiv.classList.add("sync-status-error");
        } else if (type === "success") {
            statusDiv.classList.add("sync-status-success");
        } else {
            statusDiv.classList.add("sync-status-info");
        }

        // Show progress bar when we have total and job is running
        if (progressBar) {
            if (job && job.total_items != null && job.total_items > 0) {
                progressBar.style.display = "block";
                var pct = (job.progress_percent != null ? job.progress_percent : 0) + "%";
                var fill = progressBar.querySelector(".sync-progress-fill");
                if (fill) fill.style.width = pct;
            } else {
                progressBar.style.display = "none";
            }
        }
    }

    function clearSyncStatus() {
        var container = document.getElementById("syncStatusContainer");
        if (container) {
            container.style.display = "none";
        }
    }

    function formatSourceName(source) {
        if (source === "all") return "All Sources";
        if (!source) return "Unknown";
        return source.replace(/_/g, " ").replace(/\b\w/g, function (c) { return c.toUpperCase(); });
    }

    // -----------------------------------------------------------------------
    // GOG OAuth
    // -----------------------------------------------------------------------

    var gogState = {
        authUrl: null,
        connected: false
    };

    window.openGogAuth = function() {
        if (gogState.authUrl) {
            window.open(gogState.authUrl, "_blank");
            // Show the code input step after opening auth
            var codeStep = document.getElementById("gogCodeStep");
            if (codeStep) {
                codeStep.style.display = "block";
            }
        } else {
            alert("GOG auth URL not available. Please refresh the page.");
        }
    };

    window.submitGogCode = function() {
        var input = document.getElementById("gogCodeInput");
        var statusDiv = document.getElementById("gogConnectStatus");
        var codeOrUrl = input.value.trim();

        if (!codeOrUrl) {
            if (statusDiv) {
                statusDiv.innerHTML = '<span class="text-error">Please paste the redirect URL.</span>';
            }
            return;
        }

        if (statusDiv) {
            statusDiv.innerHTML = '<span class="spinner"></span> Connecting to GOG...';
        }

        fetch(API_BASE + "/gog/exchange", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ code_or_url: codeOrUrl })
        })
            .then(function (response) {
                if (!response.ok) {
                    return response.json().then(function (data) {
                        throw new Error(data.detail || "Failed to connect GOG account");
                    });
                }
                return response.json();
            })
            .then(function (data) {
                input.value = "";

                if (data.manual_setup && data.refresh_token) {
                    // Show token for manual setup
                    if (statusDiv) {
                        statusDiv.innerHTML = '<div class="gog-manual-setup">' +
                            '<p class="text-success" style="margin-bottom:8px;">Token obtained! Add this to your config.yaml:</p>' +
                            '<pre class="gog-token-display">inputs:\n  gog:\n    refresh_token: "' + escapeHtml(data.refresh_token) + '"</pre>' +
                            '<button class="btn btn-small btn-secondary" onclick="navigator.clipboard.writeText(\'' + escapeHtml(data.refresh_token) + '\').then(function(){alert(\'Token copied!\')})">Copy Token</button>' +
                            '<p class="text-muted" style="font-size:0.85em; margin-top:8px;">After updating config.yaml, restart the server to sync.</p>' +
                            '</div>';
                    }
                } else {
                    if (statusDiv) {
                        statusDiv.innerHTML = '<span class="text-success">' + escapeHtml(data.message) + '</span>';
                    }
                    // Refresh sync sources to show the sync button
                    setTimeout(function () {
                        loadSyncSources();
                    }, 1500);
                }
            })
            .catch(function (error) {
                if (statusDiv) {
                    statusDiv.innerHTML = '<span class="text-error">' + escapeHtml(error.message) + '</span>';
                }
            });
    };

    // -----------------------------------------------------------------------
    // Enrichment Stats
    // -----------------------------------------------------------------------

    function loadEnrichmentStats() {
        var container = document.getElementById("enrichmentStats");
        var card = document.getElementById("enrichmentStatsCard");
        if (!container) return;

        fetch(API_BASE + "/enrichment/stats?user_id=" + currentUserId)
            .then(function (response) {
                if (!response.ok) throw new Error("HTTP " + response.status);
                return response.json();
            })
            .then(function (stats) {
                if (!stats.enabled) {
                    if (card) card.style.display = "none";
                    return;
                }
                if (card) card.style.display = "";
                renderEnrichmentStats(container, stats);
            })
            .catch(function (error) {
                container.innerHTML = '<div class="empty-state"><span class="text-error">Failed to load enrichment stats: ' + escapeHtml(error.message) + '</span></div>';
            });
    }

    function renderEnrichmentStats(container, stats) {
        if (stats.total === 0) {
            container.innerHTML = '<div class="empty-state"><p>No items to enrich. Sync some content first.</p></div>';
            return;
        }

        var enrichedPercent = Math.round((stats.enriched / stats.total) * 100);
        var pendingPercent = Math.round((stats.pending / stats.total) * 100);

        var html = '<div class="enrichment-summary">';

        // Main progress indicator
        html += '<div class="enrichment-progress-row">';
        html += '<div class="enrichment-progress-label">';
        html += '<span class="enrichment-count">' + stats.enriched + '/' + stats.total + '</span>';
        html += '<span class="enrichment-percent">(' + enrichedPercent + '% enriched)</span>';
        html += '</div>';
        html += '<div class="enrichment-progress-bar">';
        html += '<div class="enrichment-progress-fill enriched" style="width:' + enrichedPercent + '%"></div>';
        html += '</div>';
        html += '</div>';

        // Status breakdown
        html += '<div class="enrichment-breakdown">';

        if (stats.pending > 0) {
            html += '<div class="enrichment-stat pending">';
            html += '<span class="stat-value">' + stats.pending + '</span>';
            html += '<span class="stat-label">Pending</span>';
            html += '</div>';
        }

        if (stats.enriched > 0) {
            html += '<div class="enrichment-stat enriched">';
            html += '<span class="stat-value">' + stats.enriched + '</span>';
            html += '<span class="stat-label">Enriched</span>';
            html += '</div>';
        }

        if (stats.not_found > 0) {
            html += '<div class="enrichment-stat not-found">';
            html += '<span class="stat-value">' + stats.not_found + '</span>';
            html += '<span class="stat-label">Not Found</span>';
            html += '</div>';
        }

        if (stats.failed > 0) {
            html += '<div class="enrichment-stat failed">';
            html += '<span class="stat-value">' + stats.failed + '</span>';
            html += '<span class="stat-label">Failed</span>';
            html += '</div>';
        }

        html += '</div>';

        // Provider breakdown if available
        var providers = Object.keys(stats.by_provider || {});
        if (providers.length > 0) {
            html += '<div class="enrichment-providers">';
            html += '<span class="providers-label">By provider:</span>';
            providers.forEach(function (provider) {
                html += '<span class="provider-badge">' + provider + ': ' + stats.by_provider[provider] + '</span>';
            });
            html += '</div>';
        }

        html += '</div>';

        container.innerHTML = html;
    }

    // -----------------------------------------------------------------------
    // Enrichment Status Polling
    // -----------------------------------------------------------------------

    function startEnrichmentPolling() {
        if (enrichmentState.polling) return;
        enrichmentState.polling = true;
        enrichmentState.pollInterval = setInterval(checkEnrichmentStatus, 2000);
    }

    function stopEnrichmentPolling() {
        enrichmentState.polling = false;
        if (enrichmentState.pollInterval) {
            clearInterval(enrichmentState.pollInterval);
            enrichmentState.pollInterval = null;
        }
    }

    function checkEnrichmentStatus() {
        var statusDiv = document.getElementById("enrichmentStatus");
        var btn = document.getElementById("enrichAllBtn");

        fetch(API_BASE + "/enrichment/status")
            .then(function (response) { return response.json(); })
            .then(function (status) {
                if (status.running) {
                    // Enrichment is running - start polling and refresh stats
                    if (!enrichmentState.polling) {
                        startEnrichmentPolling();
                    }
                    if (btn) btn.disabled = true;
                    if (statusDiv) {
                        var progress = status.items_processed + "/" + status.total_items;
                        var current = status.current_item ? " - " + status.current_item : "";
                        statusDiv.innerHTML = '<span class="spinner"></span> Enriching ' + progress + current;
                    }
                    loadEnrichmentStats();
                } else if (status.completed || status.cancelled) {
                    // Enrichment finished - stop polling and refresh stats
                    stopEnrichmentPolling();
                    if (btn) btn.disabled = false;
                    if (statusDiv && status.items_processed > 0) {
                        var msg = status.cancelled ? "Enrichment cancelled" : "Enrichment complete";
                        msg += ": " + status.items_enriched + " enriched, " + status.items_not_found + " not found";
                        statusDiv.innerHTML = '<span class="text-success">' + msg + '</span>';
                    }
                    loadEnrichmentStats();
                } else {
                    // Idle - stop polling
                    stopEnrichmentPolling();
                    if (btn) btn.disabled = false;
                    if (statusDiv) statusDiv.innerHTML = "";
                }
            })
            .catch(function (error) {
                console.error("Error checking enrichment status:", error);
            });
    }

    // -----------------------------------------------------------------------
    // Manual Enrichment Trigger
    // -----------------------------------------------------------------------

    window.triggerEnrichment = function() {
        var btn = document.getElementById("enrichAllBtn");
        var select = document.getElementById("enrichTypeSelect");
        var retryCheckbox = document.getElementById("retryNotFoundCheck");
        var statusDiv = document.getElementById("enrichmentStatus");
        var contentType = select ? select.value : "";
        var retryNotFound = retryCheckbox ? retryCheckbox.checked : false;

        if (btn) btn.disabled = true;

        var body = { user_id: currentUserId, retry_not_found: retryNotFound };
        if (contentType) {
            body.content_type = contentType;
        }

        fetch(API_BASE + "/enrichment/start", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body)
        })
            .then(function (response) {
                if (!response.ok) {
                    return response.json().then(function (data) {
                        throw new Error(data.detail || "Failed to start enrichment");
                    });
                }
                return response.json();
            })
            .then(function (data) {
                if (statusDiv) {
                    statusDiv.innerHTML = '<span class="text-success">' + escapeHtml(data.message) + '</span>';
                }
                // Start polling for progress
                startEnrichmentPolling();
                checkEnrichmentStatus();
            })
            .catch(function (error) {
                if (statusDiv) {
                    statusDiv.innerHTML = '<span class="text-error">' + escapeHtml(error.message) + '</span>';
                }
            })
            .finally(function () {
                if (btn) btn.disabled = false;
            });
    };

    // -----------------------------------------------------------------------
    // Reset and Re-enrich
    // -----------------------------------------------------------------------

    window.resetAndReenrich = function() {
        var enrichBtn = document.getElementById("enrichAllBtn");
        var resetBtn = document.getElementById("resetEnrichBtn");
        var select = document.getElementById("enrichTypeSelect");
        var statusDiv = document.getElementById("enrichmentStatus");
        var contentType = select ? select.value : "";

        // Require a content type selection for reset to avoid accidental full reset
        if (!contentType) {
            if (statusDiv) {
                statusDiv.innerHTML = '<span class="text-warning">Please select a content type to reset</span>';
            }
            return;
        }

        if (enrichBtn) enrichBtn.disabled = true;
        if (resetBtn) resetBtn.disabled = true;

        if (statusDiv) {
            statusDiv.innerHTML = '<span class="spinner"></span> Resetting enrichment status...';
        }

        // First reset enrichment status
        fetch(API_BASE + "/enrichment/reset", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                content_type: contentType,
                user_id: currentUserId
            })
        })
            .then(function (response) {
                if (!response.ok) {
                    return response.json().then(function (data) {
                        throw new Error(data.detail || "Failed to reset enrichment");
                    });
                }
                return response.json();
            })
            .then(function (data) {
                if (statusDiv) {
                    statusDiv.innerHTML = '<span class="text-success">' + escapeHtml(data.message) + '</span> Starting enrichment...';
                }
                // Now start enrichment
                return fetch(API_BASE + "/enrichment/start", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({
                        content_type: contentType,
                        user_id: currentUserId,
                        retry_not_found: false
                    })
                });
            })
            .then(function (response) {
                if (!response.ok) {
                    return response.json().then(function (data) {
                        throw new Error(data.detail || "Failed to start enrichment");
                    });
                }
                return response.json();
            })
            .then(function (data) {
                if (statusDiv) {
                    statusDiv.innerHTML = '<span class="text-success">' + escapeHtml(data.message) + '</span>';
                }
                // Start polling for progress
                startEnrichmentPolling();
                checkEnrichmentStatus();
                loadEnrichmentStats();
            })
            .catch(function (error) {
                if (statusDiv) {
                    statusDiv.innerHTML = '<span class="text-error">' + escapeHtml(error.message) + '</span>';
                }
            })
            .finally(function () {
                if (enrichBtn) enrichBtn.disabled = false;
                if (resetBtn) resetBtn.disabled = false;
            });
    };

    // -----------------------------------------------------------------------
    // Edit Item Modal
    // -----------------------------------------------------------------------

    function openEditModal(dbId) {
        fetch(API_BASE + "/items/" + dbId + "?user_id=" + currentUserId)
            .then(function (response) {
                if (!response.ok) {
                    throw new Error("HTTP " + response.status);
                }
                return response.json();
            })
            .then(function (item) {
                renderEditModal(item);
            })
            .catch(function (error) {
                alert("Failed to load item: " + error.message);
            });
    }

    function renderEditModal(item) {
        // Remove any existing modal
        var existing = document.querySelector(".edit-modal");
        if (existing) existing.remove();

        var modal = document.createElement("div");
        modal.className = "edit-modal";

        var html = '<div class="edit-modal-content">';
        html += '<h3>' + escapeHtml(item.title) + '</h3>';
        html += '<div class="edit-modal-subtitle">';
        if (item.author) {
            html += escapeHtml(item.author) + ' ';
        }
        html += '<span class="badge badge-type">' + formatContentType(item.content_type) + '</span>';
        html += '</div>';

        // Status dropdown
        html += '<div class="edit-field">';
        html += '<label>Status</label>';
        html += '<select id="editStatus">';
        var statusOptions = [
            { value: "unread", label: formatStatus("unread", item.content_type) },
            { value: "currently_consuming", label: "In Progress" },
            { value: "completed", label: "Completed" }
        ];
        statusOptions.forEach(function (opt) {
            var selected = opt.value === item.status ? " selected" : "";
            html += '<option value="' + opt.value + '"' + selected + '>' + escapeHtml(opt.label) + '</option>';
        });
        html += '</select>';
        html += '</div>';

        // Star rating picker
        html += '<div class="edit-field">';
        html += '<label>Rating</label>';
        html += '<div class="star-rating">';
        html += '<div class="star-rating-stars">';
        for (var star = 1; star <= 5; star++) {
            var activeClass = item.rating && star <= item.rating ? " active" : "";
            html += '<span class="star-rating-star' + activeClass + '" data-value="' + star + '">\u2605</span>';
        }
        html += '</div>';
        html += '<button class="btn btn-small btn-clear-rating" type="button">Clear</button>';
        html += '<input type="hidden" id="editRating" value="' + (item.rating || "") + '">';
        html += '</div>';
        html += '</div>';

        // Review textarea
        html += '<div class="edit-field">';
        html += '<label>Review</label>';
        html += '<textarea id="editReview" placeholder="Write a review...">' + escapeHtml(item.review || "") + '</textarea>';
        html += '</div>';

        // Season checklist for TV shows
        if (item.content_type === "tv_show" && item.total_seasons) {
            html += '<div class="edit-field">';
            html += '<label>Seasons Watched</label>';
            html += renderSeasonChecklist(item.seasons_watched || [], item.total_seasons);
            html += '</div>';
        }

        // Actions
        html += '<div class="edit-modal-actions">';
        html += '<button class="btn btn-secondary" id="editCancel">Cancel</button>';
        html += '<button class="btn btn-primary" id="editSave">Save</button>';
        html += '</div>';
        html += '</div>';

        modal.innerHTML = html;
        document.body.appendChild(modal);
        setupEditModalListeners(item);
    }

    function renderSeasonChecklist(seasonsWatched, totalSeasons) {
        var watchedSet = {};
        seasonsWatched.forEach(function (season) { watchedSet[season] = true; });
        var watchedCount = seasonsWatched.length;

        var html = '<div class="season-controls">';
        html += '<button class="btn btn-small btn-secondary" id="seasonSelectAll" type="button">Select All</button>';
        html += '<button class="btn btn-small btn-secondary" id="seasonDeselectAll" type="button">Deselect All</button>';
        html += '<span class="season-counter" id="seasonCounter">' + watchedCount + ' / ' + totalSeasons + '</span>';
        html += '</div>';
        html += '<div class="season-grid">';
        for (var season = 1; season <= totalSeasons; season++) {
            var checked = watchedSet[season] ? true : false;
            var checkedClass = checked ? " checked" : "";
            html += '<label class="season-checkbox' + checkedClass + '">';
            html += '<input type="checkbox" name="season" value="' + season + '"' + (checked ? " checked" : "") + '>';
            html += season;
            html += '</label>';
        }
        html += '</div>';
        return html;
    }

    function setupEditModalListeners(item) {
        var modal = document.querySelector(".edit-modal");
        if (!modal) return;

        var totalSeasons = item.total_seasons || 0;

        // Star rating clicks
        modal.querySelectorAll(".star-rating-star").forEach(function (star) {
            star.addEventListener("click", function () {
                var value = parseInt(star.dataset.value);
                var ratingInput = document.getElementById("editRating");
                ratingInput.value = value;
                modal.querySelectorAll(".star-rating-star").forEach(function (other) {
                    if (parseInt(other.dataset.value) <= value) {
                        other.classList.add("active");
                    } else {
                        other.classList.remove("active");
                    }
                });
            });
        });

        // Clear rating
        var clearBtn = modal.querySelector(".btn-clear-rating");
        if (clearBtn) {
            clearBtn.addEventListener("click", function () {
                document.getElementById("editRating").value = "";
                modal.querySelectorAll(".star-rating-star").forEach(function (star) {
                    star.classList.remove("active");
                });
            });
        }

        // Season checkbox clicks
        modal.querySelectorAll(".season-checkbox input").forEach(function (checkbox) {
            checkbox.addEventListener("change", function () {
                var label = checkbox.parentElement;
                if (checkbox.checked) {
                    label.classList.add("checked");
                } else {
                    label.classList.remove("checked");
                }
                updateSeasonCounter(modal, totalSeasons);
                autoDeriveTvStatus(modal, totalSeasons);
            });
        });

        // Select All / Deselect All
        var selectAllBtn = document.getElementById("seasonSelectAll");
        if (selectAllBtn) {
            selectAllBtn.addEventListener("click", function () {
                modal.querySelectorAll(".season-checkbox input").forEach(function (checkbox) {
                    checkbox.checked = true;
                    checkbox.parentElement.classList.add("checked");
                });
                updateSeasonCounter(modal, totalSeasons);
                autoDeriveTvStatus(modal, totalSeasons);
            });
        }

        var deselectAllBtn = document.getElementById("seasonDeselectAll");
        if (deselectAllBtn) {
            deselectAllBtn.addEventListener("click", function () {
                modal.querySelectorAll(".season-checkbox input").forEach(function (checkbox) {
                    checkbox.checked = false;
                    checkbox.parentElement.classList.remove("checked");
                });
                updateSeasonCounter(modal, totalSeasons);
                autoDeriveTvStatus(modal, totalSeasons);
            });
        }

        // Cancel button
        document.getElementById("editCancel").addEventListener("click", function () {
            modal.remove();
        });

        // Backdrop click
        modal.addEventListener("click", function (event) {
            if (event.target === modal) {
                modal.remove();
            }
        });

        // Escape key
        function onEscape(event) {
            if (event.key === "Escape") {
                modal.remove();
                document.removeEventListener("keydown", onEscape);
            }
        }
        document.addEventListener("keydown", onEscape);

        // Save button
        document.getElementById("editSave").addEventListener("click", function () {
            saveItemEdit(item.db_id);
        });
    }

    function updateSeasonCounter(modal, totalSeasons) {
        var counter = document.getElementById("seasonCounter");
        if (!counter) return;
        var checked = modal.querySelectorAll(".season-checkbox input:checked").length;
        counter.textContent = checked + " / " + totalSeasons;
    }

    function autoDeriveTvStatus(modal, totalSeasons) {
        var checked = modal.querySelectorAll(".season-checkbox input:checked").length;
        var statusSelect = document.getElementById("editStatus");
        if (!statusSelect) return;

        if (checked === 0) {
            statusSelect.value = "unread";
        } else if (checked >= totalSeasons) {
            statusSelect.value = "completed";
        } else {
            statusSelect.value = "currently_consuming";
        }
    }

    function saveItemEdit(dbId) {
        var status = document.getElementById("editStatus").value;
        var ratingValue = document.getElementById("editRating").value;
        var review = document.getElementById("editReview").value;

        var body = {
            status: status,
            rating: ratingValue ? parseInt(ratingValue) : null,
            review: review || null
        };

        // Collect seasons watched if checklist exists
        var seasonCheckboxes = document.querySelectorAll(".season-checkbox input");
        if (seasonCheckboxes.length > 0) {
            var seasonsWatched = [];
            seasonCheckboxes.forEach(function (checkbox) {
                if (checkbox.checked) {
                    seasonsWatched.push(parseInt(checkbox.value));
                }
            });
            body.seasons_watched = seasonsWatched;
        }

        var saveBtn = document.getElementById("editSave");
        saveBtn.disabled = true;
        saveBtn.textContent = "Saving...";

        fetch(API_BASE + "/items/" + dbId + "?user_id=" + currentUserId, {
            method: "PATCH",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body)
        })
            .then(function (response) {
                if (!response.ok) {
                    return response.json().then(function (data) {
                        throw new Error(data.detail || "HTTP " + response.status);
                    });
                }
                return response.json();
            })
            .then(function (updatedItem) {
                updateLibraryCard(dbId, updatedItem);
                var modal = document.querySelector(".edit-modal");
                if (modal) modal.remove();
            })
            .catch(function (error) {
                alert("Failed to save: " + error.message);
                saveBtn.disabled = false;
                saveBtn.textContent = "Save";
            });
    }

    function updateLibraryCard(dbId, updatedItem) {
        var card = document.querySelector('.library-item[data-db-id="' + dbId + '"]');
        if (!card) return;

        // Update badges
        var badges = card.querySelector(".library-item-badges");
        if (badges) {
            var badgeHtml = '<span class="badge badge-type">' + formatContentType(updatedItem.content_type) + '</span>';
            badgeHtml += '<span class="badge badge-status ' + updatedItem.status + '">' + formatStatus(updatedItem.status, updatedItem.content_type) + '</span>';
            if (updatedItem.rating) {
                badgeHtml += '<span class="badge badge-rating">' + renderStars(updatedItem.rating) + '</span>';
            }
            if (updatedItem.ignored) {
                badgeHtml += '<span class="badge badge-ignored">Ignored</span>';
            }
            badges.innerHTML = badgeHtml;
        }

        // Update in-memory state
        for (var index = 0; index < libraryState.items.length; index++) {
            if (libraryState.items[index].db_id === dbId) {
                libraryState.items[index].status = updatedItem.status;
                libraryState.items[index].rating = updatedItem.rating;
                libraryState.items[index].review = updatedItem.review;
                if (updatedItem.seasons_watched !== undefined) {
                    libraryState.items[index].seasons_watched = updatedItem.seasons_watched;
                }
                break;
            }
        }
    }

    // -----------------------------------------------------------------------
    // Ignore Item
    // -----------------------------------------------------------------------

    function ignoreItem(dbId, ignored, button) {
        // Disable button during request
        button.disabled = true;
        var originalText = button.textContent;
        button.textContent = "...";

        fetch(API_BASE + "/items/" + dbId + "/ignore?user_id=" + currentUserId, {
            method: "PATCH",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ ignored: ignored })
        })
            .then(function (response) {
                if (!response.ok) {
                    return response.json().then(function (d) {
                        throw new Error(d.detail || "HTTP " + response.status);
                    });
                }
                return response.json();
            })
            .then(function (data) {
                // Update button state
                button.disabled = false;
                if (ignored) {
                    // Item is now ignored
                    button.textContent = "Unignore";
                    button.dataset.ignored = "true";
                    button.classList.remove("btn-ignore");
                    button.classList.add("btn-unignore");
                    // If in recommendations, remove the card
                    var recCard = button.closest(".rec-card");
                    if (recCard) {
                        recCard.style.opacity = "0.5";
                        recCard.style.transition = "opacity 0.3s";
                        setTimeout(function () {
                            recCard.remove();
                        }, 300);
                    }
                    // If in library, update visual state
                    var libItem = button.closest(".library-item");
                    if (libItem) {
                        libItem.classList.add("ignored");
                        // Add ignored badge if not present
                        var badges = libItem.querySelector(".library-item-badges");
                        if (badges && !badges.querySelector(".badge-ignored")) {
                            var badge = document.createElement("span");
                            badge.className = "badge badge-ignored";
                            badge.textContent = "Ignored";
                            badges.appendChild(badge);
                        }
                    }
                } else {
                    // Item is now unignored
                    button.textContent = "Ignore";
                    button.dataset.ignored = "false";
                    button.classList.remove("btn-unignore");
                    button.classList.add("btn-ignore");
                    // Update library item visual state
                    var libItem = button.closest(".library-item");
                    if (libItem) {
                        libItem.classList.remove("ignored");
                        var ignoredBadge = libItem.querySelector(".badge-ignored");
                        if (ignoredBadge) {
                            ignoredBadge.remove();
                        }
                    }
                }
            })
            .catch(function (error) {
                button.disabled = false;
                button.textContent = originalText;
                alert("Failed to update item: " + error.message);
            });
    }

    // -----------------------------------------------------------------------
    // Chat Tab
    // -----------------------------------------------------------------------

    var chatState = {
        isStreaming: false,
        currentMessageEl: null,
        pendingContentType: null
    };

    // Debounce markdown rendering to prevent visual jank during streaming.
    // Raw text is accumulated immediately; marked.parse() runs after a short
    // delay so the DOM isn't rebuilt on every tiny chunk.
    var pendingRenderEl = null;
    var renderDebounceTimer = null;

    function scheduleMarkdownRender(contentEl) {
        pendingRenderEl = contentEl;
        if (renderDebounceTimer) {
            clearTimeout(renderDebounceTimer);
        }
        renderDebounceTimer = setTimeout(flushMarkdownRender, 50);
    }

    function flushMarkdownRender() {
        if (pendingRenderEl && pendingRenderEl.dataset.rawText) {
            pendingRenderEl.innerHTML = renderMarkdown(pendingRenderEl.dataset.rawText);
        }
        pendingRenderEl = null;
        if (renderDebounceTimer) {
            clearTimeout(renderDebounceTimer);
            renderDebounceTimer = null;
        }
    }

    function setupChat() {
        var input = document.getElementById("chatInput");
        var sendBtn = document.getElementById("chatSendBtn");
        var resetBtn = document.getElementById("chatResetBtn");
        var addMemoryBtn = document.getElementById("addMemoryBtn");
        var regenerateBtn = document.getElementById("regenerateProfileBtn");

        if (!input) return; // Chat tab might not exist

        // Send on Enter (but Shift+Enter for new line)
        input.addEventListener("keydown", function (e) {
            if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                sendChatMessage();
            }
        });

        // Auto-resize textarea
        input.addEventListener("input", function () {
            this.style.height = "auto";
            this.style.height = Math.min(this.scrollHeight, 120) + "px";
        });

        sendBtn.addEventListener("click", sendChatMessage);
        resetBtn.addEventListener("click", resetChat);
        addMemoryBtn.addEventListener("click", showAddMemoryModal);
        regenerateBtn.addEventListener("click", regenerateProfile);

        // Expose for suggestion buttons
        window.sendChatSuggestion = function (text, contentType) {
            input.value = text;
            chatState.pendingContentType = contentType || null;
            sendChatMessage();
        };
    }

    function loadChatData() {
        loadMemories();
        loadProfile();
    }

    function sendChatMessage() {
        var input = document.getElementById("chatInput");
        var message = input.value.trim();
        if (!message || chatState.isStreaming) return;

        input.value = "";
        input.style.height = "auto";

        // Clear welcome message if present
        var welcome = document.querySelector(".chat-welcome");
        if (welcome) welcome.remove();

        // Add user message
        addChatMessage(message, "user");

        // Show typing indicator
        var typingEl = addTypingIndicator();

        // Start streaming response
        chatState.isStreaming = true;
        updateSendButton();

        var assistantEl = null;
        var responseText = "";

        var requestBody = {
            user_id: currentUserId,
            message: message
        };
        if (chatState.pendingContentType) {
            requestBody.content_type = chatState.pendingContentType;
        }
        chatState.pendingContentType = null;

        fetch(API_BASE + "/chat", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(requestBody)
        })
            .then(function (response) {
                if (!response.ok) {
                    throw new Error("Chat request failed");
                }

                // Some mobile browsers don't support ReadableStream from fetch.
                // Fall back to reading the full response as text.
                if (!response.body || !response.body.getReader) {
                    return response.text().then(function (text) {
                        processSSELines(text, typingEl, function (el) {
                            assistantEl = el;
                        }, function (t) {
                            responseText += t;
                        });
                        typingEl.remove();
                        chatState.isStreaming = false;
                        updateSendButton();
                    });
                }

                var reader = response.body.getReader();
                var decoder = new TextDecoder();

                function read() {
                    return reader.read().then(function (result) {
                        if (result.done) {
                            typingEl.remove();
                            chatState.isStreaming = false;
                            updateSendButton();
                            return;
                        }

                        var text = decoder.decode(result.value, { stream: true });
                        processSSELines(text, typingEl, function (el) {
                            assistantEl = el;
                        }, function (t) {
                            responseText += t;
                        });

                        return read();
                    });
                }

                return read();
            })
            .catch(function (error) {
                typingEl.remove();
                chatState.isStreaming = false;
                updateSendButton();
                addChatMessage("Sorry, I encountered an error. Please try again.", "assistant");
                console.error("Chat error:", error);
            });
    }

    function processSSELines(text, typingEl, setAssistantEl, appendText) {
        var lines = text.split("\n");
        lines.forEach(function (line) {
            if (!line.startsWith("data: ")) return;

            var data;
            try {
                data = JSON.parse(line.substring(6));
            } catch (parseError) {
                // Ignore JSON parse errors from partial SSE chunks
                return;
            }

            if (data.type === "error") {
                throw new Error(data.message || "Server error");
            }
            handleChatEvent(data, typingEl, setAssistantEl, appendText);
        });
    }

    function renderMarkdown(text) {
        if (typeof marked !== "undefined") {
            return marked.parse(text);
        }
        return escapeHtml(text).replace(/\n/g, "<br>");
    }

    function handleChatEvent(data, typingEl, setAssistantEl, appendText) {
        if (data.type === "text" && data.content) {
            typingEl.style.display = "none";

            var messagesEl = document.getElementById("chatMessages");
            var existingAssistant = messagesEl.querySelector(".chat-message.assistant:not(.typing):last-child");
            var contentEl;

            if (existingAssistant && !existingAssistant.classList.contains("tool-indicator")) {
                // Append to existing message — accumulate raw text
                contentEl = existingAssistant.querySelector(".message-content");
                contentEl.dataset.rawText = (contentEl.dataset.rawText || "") + data.content;
            } else {
                // Create new assistant message
                var el = addChatMessage("", "assistant");
                contentEl = el.querySelector(".message-content");
                contentEl.dataset.rawText = data.content;
                setAssistantEl(el);
            }
            // Debounce rendering to prevent heading/formatting jank
            scheduleMarkdownRender(contentEl);
            appendText(data.content);
            scrollChatToBottom();
        } else if (data.type === "tool_call") {
            addToolIndicator(data.tool, "executing");
        } else if (data.type === "tool_result") {
            updateToolIndicator(data.tool, data.result);
            // Refresh memories if relevant tool was called
            if (["save_memory", "mark_completed", "update_rating"].indexOf(data.tool) >= 0) {
                loadMemories();
            }
        } else if (data.type === "done") {
            flushMarkdownRender();
            typingEl.remove();
            chatState.isStreaming = false;
            updateSendButton();
        } else if (data.type === "error") {
            flushMarkdownRender();
            typingEl.remove();
            chatState.isStreaming = false;
            updateSendButton();
            addChatMessage("Error: " + data.message, "assistant");
        }
    }

    function addChatMessage(text, role) {
        var messagesEl = document.getElementById("chatMessages");
        var msgEl = document.createElement("div");
        msgEl.className = "chat-message " + role;

        var contentEl = document.createElement("div");
        contentEl.className = "message-content";
        if (role === "assistant" && text) {
            contentEl.dataset.rawText = text;
            contentEl.innerHTML = renderMarkdown(text);
        } else {
            contentEl.textContent = text;
        }
        msgEl.appendChild(contentEl);

        messagesEl.appendChild(msgEl);
        scrollChatToBottom();
        return msgEl;
    }

    function addTypingIndicator() {
        var messagesEl = document.getElementById("chatMessages");
        var el = document.createElement("div");
        el.className = "chat-message assistant typing";
        el.innerHTML = '<div class="typing-dot"></div><div class="typing-dot"></div><div class="typing-dot"></div>';
        messagesEl.appendChild(el);
        scrollChatToBottom();
        return el;
    }

    function addToolIndicator(toolName, status) {
        var messagesEl = document.getElementById("chatMessages");
        var el = document.createElement("div");
        el.className = "tool-indicator";
        el.dataset.tool = toolName;

        var text = formatToolName(toolName);
        el.innerHTML = '<span class="tool-icon">&#9881;</span> ' + text + '...';

        messagesEl.appendChild(el);
        scrollChatToBottom();
    }

    function updateToolIndicator(toolName, result) {
        var indicators = document.querySelectorAll('.tool-indicator[data-tool="' + toolName + '"]');
        indicators.forEach(function (el) {
            if (result && result.success) {
                el.classList.add("success");
                el.innerHTML = '<span class="tool-icon">&#10003;</span> ' + result.message;
            } else {
                el.innerHTML = '<span class="tool-icon">&#10007;</span> ' + (result ? result.message : "Failed");
            }
        });
    }

    function formatToolName(name) {
        var names = {
            mark_completed: "Marking as completed",
            update_rating: "Updating rating",
            add_to_wishlist: "Adding to wishlist",
            save_memory: "Saving preference",
            search_items: "Searching items",
            clarify_item: "Clarifying item"
        };
        return names[name] || name;
    }

    function scrollChatToBottom() {
        var messagesEl = document.getElementById("chatMessages");
        messagesEl.scrollTop = messagesEl.scrollHeight;
    }

    function updateSendButton() {
        var sendBtn = document.getElementById("chatSendBtn");
        sendBtn.disabled = chatState.isStreaming;
    }

    function resetChat() {
        if (!confirm("Reset the conversation? Memories will be preserved.")) return;

        fetch(API_BASE + "/chat/reset?user_id=" + currentUserId, { method: "POST" })
            .then(function (response) { return response.json(); })
            .then(function () {
                // Clear chat messages
                var messagesEl = document.getElementById("chatMessages");
                messagesEl.innerHTML = '<div class="chat-welcome">' +
                    '<h3>Chat with your recommendation advisor</h3>' +
                    '<p>Ask for recommendations, mark items as completed, or tell me about your preferences.</p>' +
                    '<div class="chat-suggestions">' +
                    '<button class="suggestion-btn" onclick="window.sendChatSuggestion(\'What game do you think will be my next obsession?\', \'video_game\')">What game will be my next obsession?</button>' +
                    '<button class="suggestion-btn" onclick="window.sendChatSuggestion(\'What book do you think I\\\'ll get lost in next?\', \'book\')">What book will I get lost in next?</button>' +
                    '<button class="suggestion-btn" onclick="window.sendChatSuggestion(\'What movie should I watch this weekend?\', \'movie\')">What movie should I watch this weekend?</button>' +
                    '<button class="suggestion-btn" onclick="window.sendChatSuggestion(\'What TV show should I binge next?\', \'tv_show\')">What TV show should I binge next?</button>' +
                    '</div></div>';
            })
            .catch(function (error) {
                alert("Failed to reset chat: " + error.message);
            });
    }

    // Memories
    function loadMemories() {
        fetch(API_BASE + "/memories?user_id=" + currentUserId + "&include_inactive=true")
            .then(function (response) { return response.json(); })
            .then(function (memories) {
                renderMemories(memories);
            })
            .catch(function () {
                document.getElementById("memoryList").innerHTML = '<div class="empty-state">Failed to load memories</div>';
            });
    }

    function renderMemories(memories) {
        var container = document.getElementById("memoryList");

        if (!memories || memories.length === 0) {
            container.innerHTML = '<div class="empty-state">No memories yet</div>';
            return;
        }

        container.innerHTML = memories.map(function (m) {
            var typeClass = m.memory_type === "user_stated" ? "user-stated" : "inferred";
            var inactiveClass = m.is_active ? "" : " inactive";
            var typeLabel = m.memory_type === "user_stated" ? "Stated" : "Inferred";

            return '<div class="memory-item ' + typeClass + inactiveClass + '" data-id="' + m.id + '">' +
                '<div class="memory-text">' + escapeHtml(m.memory_text) + '</div>' +
                '<div class="memory-meta">' +
                '<span class="memory-type">' + typeLabel + '</span>' +
                '<div class="memory-actions">' +
                '<button onclick="window.toggleMemory(' + m.id + ', ' + m.is_active + ')">' + (m.is_active ? "Disable" : "Enable") + '</button>' +
                '<button class="delete" onclick="window.deleteMemory(' + m.id + ')">Delete</button>' +
                '</div></div></div>';
        }).join("");
    }

    function showAddMemoryModal() {
        var modal = document.createElement("div");
        modal.className = "memory-modal";
        modal.innerHTML = '<div class="memory-modal-content">' +
            '<h3>Add Memory</h3>' +
            '<textarea id="newMemoryText" placeholder="e.g., I prefer shorter games during weekdays"></textarea>' +
            '<div class="memory-modal-actions">' +
            '<button class="btn btn-secondary" onclick="this.closest(\'.memory-modal\').remove()">Cancel</button>' +
            '<button class="btn btn-primary" onclick="window.saveNewMemory()">Save</button>' +
            '</div></div>';
        document.body.appendChild(modal);
        document.getElementById("newMemoryText").focus();
    }

    window.saveNewMemory = function () {
        var text = document.getElementById("newMemoryText").value.trim();
        if (!text) return;

        fetch(API_BASE + "/memories", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                user_id: currentUserId,
                memory_text: text
            })
        })
            .then(function (response) { return response.json(); })
            .then(function () {
                document.querySelector(".memory-modal").remove();
                loadMemories();
            })
            .catch(function (error) {
                alert("Failed to save memory: " + error.message);
            });
    };

    window.toggleMemory = function (id, currentlyActive) {
        fetch(API_BASE + "/memories/" + id, {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ is_active: !currentlyActive })
        })
            .then(function () { loadMemories(); })
            .catch(function (error) {
                alert("Failed to update memory: " + error.message);
            });
    };

    window.deleteMemory = function (id) {
        if (!confirm("Delete this memory?")) return;

        fetch(API_BASE + "/memories/" + id, { method: "DELETE" })
            .then(function () { loadMemories(); })
            .catch(function (error) {
                alert("Failed to delete memory: " + error.message);
            });
    };

    // Profile
    function loadProfile() {
        fetch(API_BASE + "/profile?user_id=" + currentUserId)
            .then(function (response) { return response.json(); })
            .then(function (profile) {
                renderProfile(profile);
            })
            .catch(function () {
                document.getElementById("profileSummary").innerHTML = '<div class="empty-state">Failed to load profile</div>';
            });
    }

    function renderProfile(profile) {
        var container = document.getElementById("profileSummary");

        var html = "";

        // Genre affinities
        var genres = Object.keys(profile.genre_affinities || {});
        if (genres.length > 0) {
            var topGenres = genres.slice(0, 6);
            html += '<div class="profile-section"><h5>Genres You Love</h5><div class="profile-tags">';
            topGenres.forEach(function (g) {
                html += '<span class="profile-tag">' + escapeHtml(g) + '</span>';
            });
            html += '</div></div>';
        }

        // Anti-preferences
        if (profile.anti_preferences && profile.anti_preferences.length > 0) {
            html += '<div class="profile-section"><h5>Not Your Style</h5><div class="profile-tags">';
            profile.anti_preferences.slice(0, 6).forEach(function (p) {
                html += '<span class="profile-tag anti">' + escapeHtml(p) + '</span>';
            });
            html += '</div></div>';
        }

        // Patterns
        if (profile.cross_media_patterns && profile.cross_media_patterns.length > 0) {
            html += '<div class="profile-section"><h5>Patterns</h5>';
            profile.cross_media_patterns.slice(0, 3).forEach(function (p) {
                html += '<p class="text-muted" style="font-size:0.85em;margin:4px 0;">' + escapeHtml(p) + '</p>';
            });
            html += '</div>';
        }

        if (!html) {
            html = '<div class="empty-state">No profile generated yet. Click Regenerate to analyze your preferences.</div>';
        }

        container.innerHTML = html;
    }

    function regenerateProfile() {
        var btn = document.getElementById("regenerateProfileBtn");
        btn.disabled = true;
        btn.textContent = "Generating...";

        fetch(API_BASE + "/profile/regenerate?user_id=" + currentUserId, { method: "POST" })
            .then(function (response) { return response.json(); })
            .then(function (profile) {
                renderProfile(profile);
            })
            .catch(function (error) {
                alert("Failed to regenerate profile: " + error.message);
            })
            .finally(function () {
                btn.disabled = false;
                btn.textContent = "Regenerate";
            });
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

    function updateSliderFill(slider) {
        var min = parseFloat(slider.min) || 0;
        var max = parseFloat(slider.max) || 100;
        var value = parseFloat(slider.value) || 0;
        var percent = ((value - min) / (max - min)) * 100;
        slider.style.setProperty("--value-percent", percent + "%");
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
