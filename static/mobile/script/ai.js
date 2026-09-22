(function () {
    'use strict';

    const LOGIN_PAGE = 'login.html';
    const PROFILE_PAGE = 'profile.html';

    const MobileUtils = {
        toastTimer: null,

        checkLogin() {
            const token = localStorage.getItem('token') || sessionStorage.getItem('token');
            const refreshToken = localStorage.getItem('refresh_token') || sessionStorage.getItem('refresh_token');
            const userStr = localStorage.getItem('user') || sessionStorage.getItem('user');

            if (!token && !refreshToken) {
                window.location.replace(LOGIN_PAGE);
                return null;
            }

            if (token) {
                localStorage.setItem('token', token);
                sessionStorage.setItem('token', token);
            }
            if (refreshToken) {
                localStorage.setItem('refresh_token', refreshToken);
                sessionStorage.setItem('refresh_token', refreshToken);
            }
            if (userStr) {
                localStorage.setItem('user', userStr);
                sessionStorage.setItem('user', userStr);
            }

            try {
                return userStr ? JSON.parse(userStr) : null;
            } catch (_) {
                this.logout();
                return null;
            }
        },

        getCurrentUser() {
            try {
                const userStr = localStorage.getItem('user') || sessionStorage.getItem('user');
                return userStr ? JSON.parse(userStr) : null;
            } catch (_) {
                return null;
            }
        },

        logout() {
            ['token', 'refresh_token', 'user'].forEach((key) => {
                localStorage.removeItem(key);
                sessionStorage.removeItem(key);
            });
            sessionStorage.removeItem('last_conversation_id');
            window.location.href = LOGIN_PAGE;
        },

        showMessage(message, type = 'info') {
            let toast = document.querySelector('.mobile-toast');
            if (!toast) {
                toast = document.createElement('div');
                toast.className = 'mobile-toast';
                toast.setAttribute('role', 'status');
                toast.setAttribute('aria-live', 'polite');
                document.body.appendChild(toast);
            }

            toast.className = `mobile-toast mobile-toast-${type}`;
            toast.textContent = message;
            requestAnimationFrame(() => toast.classList.add('is-visible'));

            if (this.toastTimer) window.clearTimeout(this.toastTimer);
            this.toastTimer = window.setTimeout(() => {
                toast.classList.remove('is-visible');
            }, 2800);
        },

        formatDate(date, format = 'YYYY-MM-DD') {
            if (!date) return '';
            const d = new Date(date);
            if (Number.isNaN(d.getTime())) return '';
            const pad = (value) => String(value).padStart(2, '0');
            return format
                .replace('YYYY', d.getFullYear())
                .replace('MM', pad(d.getMonth() + 1))
                .replace('DD', pad(d.getDate()))
                .replace('HH', pad(d.getHours()))
                .replace('mm', pad(d.getMinutes()))
                .replace('ss', pad(d.getSeconds()));
        },

        debounce(fn, wait) {
            let timer;
            return function debounced(...args) {
                window.clearTimeout(timer);
                timer = window.setTimeout(() => fn.apply(this, args), wait);
            };
        },

        throttle(fn, wait) {
            let locked = false;
            return function throttled(...args) {
                if (locked) return;
                locked = true;
                fn.apply(this, args);
                window.setTimeout(() => {
                    locked = false;
                }, wait);
            };
        },

        getDisplayName(user) {
            return (user && (user.full_name || user.username)) || '鐢ㄦ埛';
        }
    };

    function patchApiRedirects() {
        if (typeof APIClient === 'undefined' || !APIClient.prototype) return;
        APIClient.prototype.refreshToken = async function refreshTokenForMobile() {
            const refreshToken = localStorage.getItem('refresh_token') || sessionStorage.getItem('refresh_token');
            if (!refreshToken) {
                MobileUtils.logout();
                throw new Error('娌℃湁鍙敤鐨勫埛鏂颁护鐗?);
            }

            const response = await fetch(`${this.baseUrl}/auth/refresh`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ refresh_token: refreshToken })
            });
            const result = await response.json().catch(() => ({}));
            if (!response.ok || Number(result.code) !== 1) {
                MobileUtils.logout();
                throw new Error(result.msg || result.message || '鐧诲綍宸茶繃鏈燂紝璇烽噸鏂扮櫥褰?);
            }

            const tokenData = result.data || {};
            if (tokenData.access_token) {
                localStorage.setItem('token', tokenData.access_token);
                sessionStorage.setItem('token', tokenData.access_token);
            }
            if (tokenData.refresh_token) {
                localStorage.setItem('refresh_token', tokenData.refresh_token);
                sessionStorage.setItem('refresh_token', tokenData.refresh_token);
            }
            return tokenData.access_token;
        };
    }

    function ensureTableSpacing(text) {
        const lines = String(text || '').split('\n');
        let modified = false;
        for (let i = 0; i < lines.length; i++) {
            if (lines[i].trim().startsWith('|')) {
                let j = i;
                while (j < lines.length && lines[j].trim().startsWith('|')) j++;
                if (i > 0 && lines[i - 1].trim() !== '') {
                    lines.splice(i, 0, '');
                    j++;
                    modified = true;
                }
                if (j < lines.length && lines[j].trim() !== '') {
                    lines.splice(j, 0, '');
                    modified = true;
                }
                i = j;
            }
        }
        return modified ? lines.join('\n') : text;
    }

    function sanitizeStreamingContent(text) {
        if (!text || typeof text !== 'string') return '';
        let output = text.replace(/!\[[^\]]*\]\([^)]*\)/g, '');
        const lastImageStart = output.lastIndexOf('![');
        if (lastImageStart !== -1) {
            const tail = output.slice(lastImageStart);
            if (!tail.includes(')')) output = output.slice(0, lastImageStart);
        }
        output = output.replace(/^.*(?:img_url|image_url|閰嶅浘璺緞|鏈枃閰嶅浘璺緞).*$(\r?\n)?/gmi, '');
        output = output.replace(/[A-Za-z]:[/\\][^\s)\]]+/g, '');
        output = output.replace(/\/upload\/[^\s)\]]+/g, '');
        output = output.replace(/\bupload\/(?:images|ask)\/[^\s)\]]+/g, '');
        return output.trimEnd();
    }

    const MarkdownParser = {
        init() {
            if (typeof marked === 'undefined') return;
            marked.setOptions({
                gfm: true,
                breaks: true,
                headerIds: false,
                mangle: false,
                highlight(code, lang) {
                    if (typeof hljs !== 'undefined' && lang && hljs.getLanguage(lang)) {
                        try {
                            return hljs.highlight(code, { language: lang }).value;
                        } catch (_) {
                            return code;
                        }
                    }
                    return code;
                }
            });
        },

        render(text) {
            if (!text) return '';
            try {
                const rawHtml = typeof marked !== 'undefined'
                    ? marked.parse(ensureTableSpacing(text))
                    : this.escapeHtml(text).replace(/\n/g, '<br>');
                if (typeof DOMPurify === 'undefined') return rawHtml;
                return DOMPurify.sanitize(rawHtml, {
                    ALLOWED_TAGS: [
                        'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'p', 'br', 'span', 'div',
                        'strong', 'em', 'b', 'i', 'u', 's', 'ul', 'ol', 'li', 'blockquote',
                        'pre', 'code', 'a', 'img', 'table', 'thead', 'tbody', 'tr', 'th', 'td', 'hr'
                    ],
                    ALLOWED_ATTR: ['href', 'src', 'alt', 'title', 'class', 'id', 'target', 'rel'],
                    ALLOWED_URI_REGEXP: /^(?:(?:https?|mailto|ftp):|[^a-z]|[a-z+.-]+(?:[^a-z+.-:]|$))/i
                });
            } catch (_) {
                return this.escapeHtml(text).replace(/\n/g, '<br>');
            }
        },

        escapeHtml(text) {
            const div = document.createElement('div');
            div.textContent = text;
            return div.innerHTML;
        }
    };

    class MobileAIConversationSystem {
        constructor() {
            this.currentConversationId = null;
            this.currentAttachments = [];
            this.currentPage = 1;
            this.pageSize = 8;
            this.totalPages = 1;
            this.isLoadingMore = false;
            this.hasMore = true;
            this.searchMode = false;
            this.scrollListener = null;
            this.supportsSpeechRecognition = false;
            this.speechRecognition = null;
            this.isListening = false;
            this.speechBaseText = '';
            this.speechFinalText = '';
        }

        async init() {
            patchApiRedirects();
            const user = MobileUtils.checkLogin();
            this.renderUserEntry(user);
            this.bindEvents();
            this.initVoiceFeatures();
            this.setupInfiniteScroll();
            await this.loadHistoryList(1);

            const lastConversationId = sessionStorage.getItem('last_conversation_id');
            if (lastConversationId) {
                await this.loadConversation(Number(lastConversationId), { keepDrawer: true });
            }
        }

        renderUserEntry(user) {
            const currentUser = user || MobileUtils.getCurrentUser();
            const name = MobileUtils.getDisplayName(currentUser);
            const avatar = document.getElementById('topUserAvatar');
            const userName = document.getElementById('topUserName');
            if (avatar) avatar.textContent = name.charAt(0).toUpperCase();
            if (userName) userName.textContent = name;
        }

        bindEvents() {
            const bindClick = (id, fn) => {
                const el = document.getElementById(id);
                if (el) el.addEventListener('click', fn);
            };

            bindClick('openHistoryBtn', () => this.openDrawer());
            bindClick('closeHistoryBtn', () => this.closeDrawer());
            bindClick('drawerOverlay', () => this.closeDrawer());
            bindClick('newConversationBtn', () => this.createNewConversation());
            bindClick('metaNewConversationBtn', () => this.createNewConversation());
            bindClick('emptyNewConversationBtn', () => this.createNewConversation());
            bindClick('sendButton', () => this.sendMessage());
            bindClick('attachmentButton', () => document.getElementById('fileInput')?.click());
            bindClick('voiceInputButton', () => this.toggleVoiceInput());

            const profileEntry = document.getElementById('profileEntry');
            if (profileEntry) profileEntry.href = PROFILE_PAGE;

            const historySearch = document.getElementById('historySearch');
            if (historySearch) {
                historySearch.addEventListener('input', MobileUtils.debounce(() => this.loadHistoryList(1), 500));
            }

            const messageInput = document.getElementById('messageInput');
            if (messageInput) {
                messageInput.addEventListener('keydown', (event) => {
                    if (event.key === 'Enter' && !event.shiftKey && !event.ctrlKey) {
                        event.preventDefault();
                        this.sendMessage();
                    }
                });
                messageInput.addEventListener('input', () => this.autoResizeInput());
            }

            const fileInput = document.getElementById('fileInput');
            if (fileInput) {
                fileInput.addEventListener('change', () => {
                    this.handleFileUpload(fileInput.files);
                    fileInput.value = '';
                });
            }
        }

        openDrawer() {
            const drawer = document.getElementById('historyDrawer');
            const overlay = document.getElementById('drawerOverlay');
            if (overlay) overlay.hidden = false;
            requestAnimationFrame(() => drawer?.classList.add('is-open'));
            if (drawer) drawer.setAttribute('aria-hidden', 'false');
        }

        closeDrawer() {
            const drawer = document.getElementById('historyDrawer');
            const overlay = document.getElementById('drawerOverlay');
            drawer?.classList.remove('is-open');
            if (drawer) drawer.setAttribute('aria-hidden', 'true');
            window.setTimeout(() => {
                if (overlay) overlay.hidden = true;
            }, 240);
        }

        setupInfiniteScroll() {
            const historyList = document.getElementById('historyList');
            if (!historyList) return;
            if (this.scrollListener) historyList.removeEventListener('scroll', this.scrollListener);
            this.scrollListener = MobileUtils.throttle(() => {
                if (this.searchMode || !this.hasMore || this.isLoadingMore) return;
                const nearBottom = historyList.scrollHeight - historyList.scrollTop - historyList.clientHeight < 60;
                if (nearBottom) this.loadMoreHistory();
            }, 200);
            historyList.addEventListener('scroll', this.scrollListener);
        }

        async loadMoreHistory() {
            if (this.isLoadingMore || !this.hasMore || this.searchMode) return;
            this.isLoadingMore = true;
            this.showHistoryFooter('鍔犺浇涓?..');
            try {
                await this.loadHistoryList(this.currentPage + 1, true);
            } finally {
                this.isLoadingMore = false;
            }
        }

        showHistoryFooter(text) {
            const footer = document.getElementById('historyListFooter');
            if (footer) footer.textContent = text || '';
        }

        async loadHistoryList(page = 1, append = false) {
            const container = document.getElementById('historyList');
            const searchInput = document.getElementById('historySearch');
            if (!container) return;

            const query = searchInput ? searchInput.value.trim() : '';
            this.searchMode = Boolean(query);
            if (!append) {
                container.innerHTML = '';
                this.currentPage = 1;
                this.hasMore = true;
                this.showHistoryFooter('');
            }

            try {
                let conversations = [];
                let totalPages = 1;

                if (query) {
                    const response = await conversationAPI.searchConversations(query);
                    conversations = this.extractConversationArray(response);
                    this.hasMore = false;
                } else {
                    const response = await conversationAPI.getHistoryPage({ page, size: this.pageSize });
                    conversations = this.extractConversationArray(response);
                    totalPages = Number(response?.total_pages || Math.ceil((response?.total_count || conversations.length) / this.pageSize) || 1);
                }

                conversations.sort((a, b) => new Date(b.updated_time || 0) - new Date(a.updated_time || 0));

                if (append) this.appendConversations(conversations, container);
                else this.renderConversationList(conversations, container);

                this.currentPage = page;
                this.totalPages = totalPages;
                if (!this.searchMode) this.hasMore = this.currentPage < this.totalPages;
                this.showHistoryFooter(this.hasMore ? '' : (conversations.length ? '娌℃湁鏇村瀵硅瘽浜? : ''));
            } catch (error) {
                container.innerHTML = `<div class="history-error"><i class="fas fa-exclamation-triangle"></i><p>鍔犺浇澶辫触锛?{this.escapeHtml(error.message)}</p></div>`;
                this.showHistoryFooter('');
            }
        }

        extractConversationArray(response) {
            if (Array.isArray(response)) return response;
            if (!response || typeof response !== 'object') return [];
            if (Array.isArray(response.history)) return response.history;
            if (Array.isArray(response.sessions)) return response.sessions;
            if (Array.isArray(response.data)) return response.data;
            if (Array.isArray(response.data?.history)) return response.data.history;
            if (Array.isArray(response.data?.sessions)) return response.data.sessions;
            return [];
        }

        renderConversationList(conversations, container) {
            if (!conversations.length) {
                container.innerHTML = '<div class="empty-history"><i class="fas fa-comments"></i><p>鏆傛棤瀵硅瘽</p></div>';
                return;
            }
            container.innerHTML = conversations.map((conv) => this.generateHistoryItemHTML(conv)).join('');
            this.attachHistoryItemEvents(container);
        }

        appendConversations(conversations, container) {
            if (!conversations.length) return;
            const wrapper = document.createElement('div');
            wrapper.innerHTML = conversations.map((conv) => this.generateHistoryItemHTML(conv)).join('');
            Array.from(wrapper.children).forEach((child) => container.appendChild(child));
            this.attachHistoryItemEvents(container);
        }

        generateHistoryItemHTML(conv) {
            const id = Number(conv.id);
            const title = this.escapeHtml(conv.title || conv.name || '鏃犳爣棰樺璇?);
            const date = conv.updated_time ? MobileUtils.formatDate(conv.updated_time, 'MM-DD HH:mm') : '';
            const active = id === this.currentConversationId ? ' active' : '';
            return `
                <div class="history-item${active}" data-id="${id}" role="listitem">
                    <div class="history-item-main">
                        <div class="history-item-title">${title}</div>
                        <div class="history-item-date">${date}</div>
                    </div>
                    <div class="history-item-actions">
                        <button class="btn-edit-title" type="button" title="淇敼鏍囬" aria-label="淇敼鏍囬"><i class="fas fa-edit"></i></button>
                        <button class="btn-delete-conversation" type="button" title="鍒犻櫎瀵硅瘽" aria-label="鍒犻櫎瀵硅瘽"><i class="fas fa-trash"></i></button>
                    </div>
                </div>`;
        }

        attachHistoryItemEvents(container) {
            container.querySelectorAll('.history-item').forEach((item) => {
                const id = Number(item.dataset.id);
                item.querySelector('.history-item-main')?.addEventListener('click', () => this.loadConversation(id));
                item.querySelector('.btn-edit-title')?.addEventListener('click', (event) => {
                    event.stopPropagation();
                    const title = item.querySelector('.history-item-title')?.textContent || '';
                    this.editConversationTitle(id, title);
                });
                item.querySelector('.btn-delete-conversation')?.addEventListener('click', (event) => {
                    event.stopPropagation();
                    this.deleteConversation(id);
                });
            });
        }

        async createNewConversation() {
            try {
                MobileUtils.showMessage('姝ｅ湪鍒涘缓鏂板璇?..', 'info');
                const response = await conversationAPI.createConversation();
                const conversation = this.extractSingleConversation(response);
                if (!conversation || !conversation.id) throw new Error('鍒涘缓瀵硅瘽澶辫触');
                await this.loadConversation(Number(conversation.id), { keepDrawer: false });
                await this.loadHistoryList(1);
                this.clearInputAndAttachments();
                MobileUtils.showMessage('鏂板璇濆凡鍒涘缓', 'success');
            } catch (error) {
                MobileUtils.showMessage(`鍒涘缓瀵硅瘽澶辫触锛?{error.message}`, 'error');
            }
        }

        extractSingleConversation(response) {
            if (!response) return null;
            if (response.id !== undefined) return response;
            if (response.data?.id !== undefined) return response.data;
            return null;
        }

        async loadConversation(conversationId, options = {}) {
            if (!conversationId || Number.isNaN(conversationId)) {
                MobileUtils.showMessage('鏃犳晥鐨勫璇滻D', 'error');
                return;
            }

            try {
                const response = await conversationAPI.getConversationById(conversationId);
                const conversation = this.extractSingleConversation(response);
                if (!conversation) throw new Error('瀵硅瘽涓嶅瓨鍦ㄦ垨鏃犳潈闄愯闂?);

                this.currentConversationId = conversationId;
                sessionStorage.setItem('last_conversation_id', String(conversationId));
                this.updateConversationHeader(conversation);
                this.showInputSection(true);

                const messagesResponse = await messageAPI.getMessagesByConversation(conversationId);
                const messages = Array.isArray(messagesResponse) ? messagesResponse : this.extractMessageArray(messagesResponse);
                this.loadMessages(messages);
                await this.loadHistoryList(1);
                if (!options.keepDrawer) this.closeDrawer();
            } catch (error) {
                MobileUtils.showMessage(`鍔犺浇瀵硅瘽澶辫触锛?{error.message}`, 'error');
                this.showEmptyState();
            }
        }

        extractMessageArray(response) {
            if (Array.isArray(response)) return response;
            if (Array.isArray(response?.data)) return response.data;
            if (Array.isArray(response?.messages)) return response.messages;
            if (Array.isArray(response?.data?.messages)) return response.data.messages;
            return [];
        }

        updateConversationHeader(conversation) {
            const title = conversation.title || conversation.name || '鏃犳爣棰樺璇?;
            const titleEl = document.getElementById('currentConversationTitle');
            const pageTitle = document.getElementById('mobilePageTitle');
            const dateEl = document.getElementById('currentConversationDate');
            if (titleEl) titleEl.textContent = title;
            if (pageTitle) pageTitle.textContent = title;
            if (dateEl) dateEl.textContent = conversation.updated_time ? MobileUtils.formatDate(conversation.updated_time, 'YYYY-MM-DD HH:mm:ss') : '';
        }

        loadMessages(messages) {
            const container = document.getElementById('conversationContent');
            if (!container) return;
            container.innerHTML = '';

            console.log('[FeedbackFrontend][mobile] loaded messages:', (messages || []).map((message) => ({
                id: message?.id,
                role: message?.role,
                feedback_eligible: message?.feedback_eligible,
                has_reference_docs: Boolean(message?.ai_reference_doc_ids),
            })));

            if (!messages || messages.length === 0) {
                container.innerHTML = '<div class="empty-state"><div class="empty-icon"><i class="fas fa-comments"></i></div><h2>瀵硅瘽寮€濮?/h2><p>杩欐槸鎮ㄧ殑鏂板璇濓紝璇疯緭鍏ユ偍鐨勯棶棰樺紑濮嬩氦娴併€?/p></div>';
                return;
            }

            const fragment = document.createDocumentFragment();
            messages.forEach((message) => fragment.appendChild(this.createMessageElement(message)));
            container.appendChild(fragment);
            window.setTimeout(() => this.scrollToBottom(), 80);
        }

        createMessageElement(message) {
            const isAI = Number(message.role) === 0;
            console.log('[FeedbackFrontend][mobile] render message:', {
                id: message?.id,
                isAI,
                feedback_eligible: message?.feedback_eligible,
                has_reference_docs: Boolean(message?.ai_reference_doc_ids),
                will_render_feedback: isAI && message?.feedback_eligible === true,
            });
            const container = document.createElement('div');
            container.className = `message-container ${isAI ? 'ai' : 'user'}`;

            let html = `<div class="message-sender ${isAI ? 'ai' : 'user'}">${isAI ? 'AI鍔╂墜' : '鐢ㄦ埛'}</div>`;
            html += `<div class="message-content ${isAI ? 'ai' : 'user'}">`;
            const content = message.content_text || message.content || '';
            const isCompletedAI = isAI && content && !content.includes('回答生成中，请稍后刷新') && !content.includes('回答生成失败');
            if (isCompletedAI) {
                const process = Array.isArray(message.thinking_process) ? message.thinking_process : [];
                const steps = process.map((step) => `<div class="ai-process-step is-complete"><span class="ai-process-step-mark">✓</span><span><b>${step.title || '处理完成'}</b><small>${step.detail || ''}</small></span></div>`).join('');
                html += `<details class="ai-process is-complete"><summary><span class="ai-process-icon">◇</span><span>已经完成思考</span><span class="ai-process-chevron">›</span></summary>${steps ? `<div class="ai-process-steps">${steps}</div>` : ''}</details>`;
            }
            html += `<div class="message-text">${this.formatMessageContent(content || '鏆傛棤鍐呭', isAI)}</div>`;

            if (!isAI && message.user_uploaded_images && String(message.user_uploaded_images).trim()) {
                const images = String(message.user_uploaded_images).split(',').map((item) => item.trim()).filter(Boolean);
                if (images.length) {
                    html += '<div class="message-image-previews">';
                    images.forEach((imageUrl) => {
                        const fullUrl = imageUrl.startsWith('data:image') ? imageUrl : API_CONFIG.getAssetUrl(imageUrl);
                        const fileName = this.escapeHtml(imageUrl.split('/').pop() || '鍥剧墖');
                        html += `<div class="image-preview-item" data-image-url="${this.escapeAttribute(imageUrl)}"><img src="${this.escapeAttribute(fullUrl)}" alt="${fileName}"></div>`;
                    });
                    html += '</div>';
                }
            }

            const messageTime = message.created_time ? MobileUtils.formatDate(message.created_time, 'YYYY-MM-DD HH:mm') : '';
            html += `<div class="message-time">${messageTime}</div>`;
            if (isAI && message.feedback_eligible === true) {
                html += this.renderFeedbackControls(message);
            }
            html += '</div>';
            container.innerHTML = html;

            if (isAI && message.feedback_eligible === true) {
                container.addEventListener('click', async (event) => {
                    const actionButton = event.target.closest('[data-feedback-action]');
                    const reasonButton = event.target.closest('[data-feedback-reason]');
                    const submitButton = event.target.closest('[data-feedback-submit]');
                    if (!actionButton && !reasonButton && !submitButton) return;
                    event.preventDefault();
                    event.stopPropagation();

                    const reasons = container.querySelector('.feedback-reasons');
                    try {
                        if (actionButton) {
                            const action = actionButton.dataset.feedbackAction;
                            if (action === 'negative') {
                                if (reasons) reasons.hidden = !reasons.hidden;
                                actionButton.classList.toggle('is-selected', reasons && !reasons.hidden);
                                return;
                            }
                            if (await this.submitMessageFeedback(message, 'positive', 'AI回复有帮助')) {
                                this.lockFeedbackControls(container, '已反馈：这条回答有帮助', 'positive');
                            }
                            return;
                        }

                        if (reasonButton) {
                            container.querySelectorAll('[data-feedback-reason]').forEach((button) => button.classList.remove('is-selected'));
                            reasonButton.classList.add('is-selected');
                            const otherInput = container.querySelector('.feedback-other');
                            if (reasonButton.dataset.feedbackReason === 'other') {
                                if (otherInput) otherInput.hidden = false;
                                return;
                            }
                            if (otherInput) otherInput.hidden = true;
                            const reasonKey = reasonButton.dataset.feedbackReason;
                            const mapped = this.mapFeedbackReason(reasonKey);
                            const reasonLabel = reasonButton.textContent.trim();
                            if (await this.submitMessageFeedback(message, mapped.type, `原因：${reasonLabel}；${mapped.comment}`)) {
                                this.lockFeedbackControls(container, `已反馈：${reasonLabel}`, 'negative');
                            }
                            return;
                        }

                        if (submitButton) {
                            const input = container.querySelector('.feedback-other-input');
                            const comment = input ? input.value.trim() : '';
                            if (!comment) {
                                input?.focus();
                                MobileUtils.showMessage('请先补充问题说明', 'warning');
                                return;
                            }
                            if (await this.submitMessageFeedback(message, 'negative', `原因：其他；${comment}`)) {
                                this.lockFeedbackControls(container, '已反馈：其他问题', 'negative');
                            }
                        }
                    } catch (error) {
                        console.error('提交反馈失败:', error);
                        MobileUtils.showMessage('反馈提交失败，请稍后重试', 'error');
                    }
                });
            }

            container.querySelectorAll('.image-preview-item').forEach((item) => {
                item.addEventListener('click', () => this.previewImage(item.dataset.imageUrl));
            });

            if (isAI) {
                this.bindInlineMessageImages(container);
            }

            if (isAI && message.ai_reference_doc_ids && String(message.ai_reference_doc_ids).trim()) {
                this.loadAndDisplayDocuments(container, message.ai_reference_doc_ids);
            }

            return container;
        }

        renderFeedbackControls(message) {
            const messageId = Number(message && message.id ? message.id : 0);
            if (!messageId) return '';
            return `
                <div class="message-feedback" data-message-id="${messageId}">
                    <div class="message-feedback-controls">
                        <span class="feedback-prompt">这条回答有帮助吗？</span>
                        <button type="button" class="feedback-action-button" data-feedback-action="positive" title="有帮助" aria-label="有帮助"><i class="fas fa-thumbs-up"></i></button>
                        <button type="button" class="feedback-action-button" data-feedback-action="negative" title="需要改进" aria-label="需要改进"><i class="fas fa-thumbs-down"></i></button>
                    </div>
                    <div class="feedback-reasons" hidden>
                        <div class="feedback-reason-title">请选择问题类型</div>
                        <div class="feedback-reason-list">
                            <button type="button" class="feedback-reason-button" data-feedback-reason="incorrect">内容错误</button>
                            <button type="button" class="feedback-reason-button" data-feedback-reason="insufficient">信息不完整</button>
                            <button type="button" class="feedback-reason-button" data-feedback-reason="irrelevant">与问题无关</button>
                            <button type="button" class="feedback-reason-button" data-feedback-reason="unclear">表达不清楚</button>
                            <button type="button" class="feedback-reason-button" data-feedback-reason="other">其他问题</button>
                        </div>
                        <div class="feedback-other" hidden>
                            <input class="feedback-other-input" type="text" maxlength="300" placeholder="请简单说明问题...">
                            <button type="button" class="feedback-submit-button" data-feedback-submit>提交</button>
                        </div>
                    </div>
                    <div class="feedback-status" aria-live="polite"></div>
                </div>
            `;
        }

        mapFeedbackReason(reasonKey) {
            const map = {
                incorrect: { type: 'correction', comment: '内容存在错误' },
                insufficient: { type: 'additional_information', comment: '当前回复证据不足' },
                irrelevant: { type: 'irrelevant', comment: '回复偏离问题' },
                unclear: { type: 'negative', comment: '回复表达不够清晰' },
                other: { type: 'negative', comment: '其他原因' },
            };
            return map[reasonKey] || map.other;
        }

        async submitMessageFeedback(message, feedbackType, comment) {
            const currentUser = MobileUtils.getCurrentUser();
            const userId = Number(currentUser && currentUser.id ? currentUser.id : 0);
            const conversationId = Number(message.session_id || message.conversation_id || this.currentConversationId || 0);
            const messageId = Number(message.id || 0);

            if (!userId) {
                MobileUtils.showMessage('请先登录后再反馈', 'warning');
                return false;
            }
            if (!conversationId || !messageId) {
                MobileUtils.showMessage('无法定位这条回复，暂时不能反馈', 'warning');
                return false;
            }

            const response = await fetch('/api/v1/feedback/ingest', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'Authorization': `Bearer ${localStorage.getItem('token') || sessionStorage.getItem('token') || ''}`,
                },
                body: JSON.stringify({
                    user_id: userId,
                    conversation_id: conversationId,
                    message_id: messageId,
                    feedback_type: feedbackType,
                    comment: comment || null,
                }),
            });

            let payload = null;
            try {
                payload = await response.json();
            } catch (_) {
                payload = null;
            }

            if (!response.ok) {
                throw new Error(payload?.message || payload?.msg || `反馈提交失败（${response.status}）`);
            }

            MobileUtils.showMessage('反馈已提交', 'success');
            return true;
        }

        lockFeedbackControls(container, text, action) {
            const controls = container.querySelector('.message-feedback-controls');
            const reasons = container.querySelector('.feedback-reasons');
            if (controls) {
                controls.querySelectorAll('button').forEach((button) => {
                    button.disabled = true;
                });
            }
            if (action) {
                const selected = controls?.querySelector(`[data-feedback-action="${action}"]`);
                selected?.classList.add('is-selected');
            }
            if (reasons) {
                reasons.hidden = true;
                reasons.querySelectorAll('button, input').forEach((element) => element.disabled = true);
            }
            const status = container.querySelector('.feedback-status');
            if (status) status.textContent = text || '已提交反馈';
        }

        formatMessageContent(content, isAI) {
            if (isAI) return MarkdownParser.render(content);
            return this.escapeHtml(content).replace(/\n/g, '<br>');
        }
        normalizeReferenceDocuments(referenceInput) {
            if (!referenceInput) return [];
            const normalizeDocId = (value) => {
                const raw = String(value || '').trim();
                const parts = raw.includes(':') ? raw.split(':') : ['', raw];
                return parseInt(parts[parts.length - 1], 10);
            };

            if (Array.isArray(referenceInput)) {
                return referenceInput.map((doc) => ({
                    doc_id: normalizeDocId(doc.doc_id),
                    library_type: doc.library_type === 'knowledge' ? 'knowledge' : 'breakdown',
                    title: doc.title || doc.doc_name || '',
                    score: doc.score !== undefined && doc.score !== null ? Number(doc.score) : null
                })).filter((doc) => Number.isInteger(doc.doc_id));
            }

            if (typeof referenceInput === 'object') {
                if (Array.isArray(referenceInput.doc_aggs)) return this.normalizeReferenceDocuments(referenceInput.doc_aggs);
                if (Array.isArray(referenceInput.documents)) return this.normalizeReferenceDocuments(referenceInput.documents);
            }

            if (typeof referenceInput === 'string') {
                const raw = referenceInput.trim();
                if (!raw) return [];
                if (raw.startsWith('[') || raw.startsWith('{')) {
                    try {
                        return this.normalizeReferenceDocuments(JSON.parse(raw));
                    } catch (_) {
                        // 鍏煎鏃ф牸寮?
                    }
                }
                return raw.split(',').map((value) => {
                    const trimmed = value.trim();
                    const parts = trimmed.includes(':') ? trimmed.split(':') : ['breakdown', trimmed];
                    return {
                        doc_id: normalizeDocId(trimmed),
                        library_type: parts[0] === 'knowledge' ? 'knowledge' : 'breakdown',
                        title: '',
                        score: null
                    };
                }).filter((doc) => Number.isInteger(doc.doc_id));
            }
            return [];
        }

        renderReferenceDocumentsHtml(docs) {
            const items = docs.map((doc) => {
                const title = this.escapeHtml(doc.title && doc.title.trim() ? doc.title : `鏂囨。 ${doc.doc_id}`);
                const libraryType = doc.library_type === 'knowledge' ? 'knowledge' : 'breakdown';
                const scoreHtml = typeof doc.score === 'number' && !Number.isNaN(doc.score)
                    ? `<span class="doc-score ${this.getReferenceScoreLevel(doc.score).className}">鍖归厤搴?${(Math.max(0, Math.min(1, doc.score)) * 100).toFixed(1)}%</span>`
                    : '';
                return `<div class="document-item document-item-static" data-doc-id="${doc.doc_id}" data-library-type="${libraryType}"><div class="document-item-title"><i class="fas fa-book-open"></i><span class="doc-title">${title}</span>${scoreHtml}</div></div>`;
            }).join('');
            return `<div class="message-documents"><div class="documents-title"><i class="fas fa-book-open"></i> 鐩稿叧鍙傝€冩枃妗?/div><div class="documents-list">${items}</div></div>`;
        }

        getReferenceScoreLevel(score) {
            const value = Math.max(0, Math.min(1, Number(score) || 0));
            if (value >= 0.8) return { className: 'score-high' };
            if (value >= 0.6) return { className: 'score-medium' };
            return { className: 'score-low' };
        }

        loadAndDisplayDocuments(container, referenceInput) {
            if (!container || container.querySelector('.message-documents')) return;
            const docs = this.normalizeReferenceDocuments(referenceInput);
            if (!docs.length) return;
            const contentDiv = container.querySelector('.message-content');
            if (contentDiv) contentDiv.insertAdjacentHTML('beforeend', this.renderReferenceDocumentsHtml(docs));
        }

        async editConversationTitle(conversationId, currentTitle) {
            const newTitle = window.prompt('璇疯緭鍏ユ柊鐨勫璇濇爣棰?, currentTitle || '');
            if (!newTitle || !newTitle.trim() || newTitle.trim() === currentTitle) return;
            try {
                await conversationAPI.updateTitle(conversationId, newTitle.trim());
                if (this.currentConversationId === conversationId) {
                    this.updateConversationHeader({ id: conversationId, title: newTitle.trim(), updated_time: new Date() });
                }
                await this.loadHistoryList(1);
                MobileUtils.showMessage('鏍囬宸叉洿鏂?, 'success');
            } catch (error) {
                MobileUtils.showMessage(`鏇存柊鏍囬澶辫触锛?{error.message}`, 'error');
            }
        }

        async deleteConversation(conversationId) {
            if (!window.confirm('纭畾瑕佸垹闄よ繖涓璇濆悧锛熸鎿嶄綔涓嶅彲鎭㈠銆?)) return;
            try {
                await conversationAPI.deleteConversation(conversationId);
                if (this.currentConversationId === conversationId) {
                    this.currentConversationId = null;
                    sessionStorage.removeItem('last_conversation_id');
                    this.showEmptyState();
                }
                await this.loadHistoryList(1);
                MobileUtils.showMessage('瀵硅瘽宸插垹闄?, 'success');
            } catch (error) {
                MobileUtils.showMessage(`鍒犻櫎瀵硅瘽澶辫触锛?{error.message}`, 'error');
            }
        }

        async sendMessage() {
            if (this.isListening) this.stopVoiceInput();
            const input = document.getElementById('messageInput');
            const sendButton = document.getElementById('sendButton');
            const messageText = input ? input.value.trim() : '';

            if (!messageText && this.currentAttachments.length === 0) {
                MobileUtils.showMessage('璇疯緭鍏ユ秷鎭垨涓婁紶鍥剧墖', 'warning');
                return;
            }
            if (!this.currentConversationId) {
                MobileUtils.showMessage('璇峰厛閫夋嫨鎴栧垱寤轰竴涓璇?, 'warning');
                return;
            }

            if (sendButton) sendButton.disabled = true;
            const attachmentsToSend = [...this.currentAttachments];
            let tempImageUrls = [];
            let uploadedImageUrls = [];
            this.clearInputAndAttachments();

            try {
                if (attachmentsToSend.length) {
                    tempImageUrls = await Promise.all(attachmentsToSend.map((file) => this.readFileAsDataUrl(file)));
                    const uploadResp = await messageAPI.uploadImages(attachmentsToSend);
                    uploadedImageUrls = this.extractUploadedImageUrls(uploadResp);
                }

                this.addTemporaryUserMessage(messageText, tempImageUrls);
                this.showAIStreamingMessage();

                await messageAPI.askStream(
                    {
                        session_id: this.currentConversationId,
                        content_text: messageText,
                        user_uploaded_images: uploadedImageUrls.join(', ')
                    },
                    (chunk) => {
                        if (chunk.reference_docs) this.updateReferenceDocuments(chunk.reference_docs);
                        if (chunk.answer !== undefined) this.updateStreamingMessage(chunk.answer, chunk.final === true);
                    },
                    async () => {
                        console.log('[FeedbackFrontend][mobile] stream complete, reload conversation:', this.currentConversationId);
                        this.removeTemporaryElements();
                        this.removeStreamingMessage();
                        await this.loadConversation(this.currentConversationId, { keepDrawer: true });
                    },
                    (error) => {
                        this.removeStreamingMessage();
                        this.removeTemporaryElements();
                        MobileUtils.showMessage(`鍙戦€佹秷鎭け璐ワ細${error.message}`, 'error');
                    },
                    (status) => this.updateAIStatus(status)
                );
            } catch (error) {
                MobileUtils.showMessage(`鍙戦€佹秷鎭け璐ワ細${error.message}`, 'error');
                if (this.currentConversationId) await this.loadConversation(this.currentConversationId, { keepDrawer: true });
            } finally {
                if (sendButton) sendButton.disabled = false;
            }
        }

        extractUploadedImageUrls(uploadResp) {
            const list = Array.isArray(uploadResp) ? uploadResp : (Array.isArray(uploadResp?.data) ? uploadResp.data : []);
            if (uploadResp?.url) return [uploadResp.url];
            return list.map((item) => item.url || item.path || item).filter(Boolean);
        }

        readFileAsDataUrl(file) {
            return new Promise((resolve) => {
                const reader = new FileReader();
                reader.onload = (event) => resolve(event.target.result);
                reader.readAsDataURL(file);
            });
        }

        showAIStreamingMessage() {
            const container = document.getElementById('conversationContent');
            if (!container) return;
            const tempDiv = document.createElement('div');
            tempDiv.className = 'message-container ai';
            tempDiv.id = 'streamingAIMessage';
            tempDiv.innerHTML = '<div class="message-sender ai">AI助手</div><div class="message-content ai"><div class="message-text streaming-text"></div><div class="message-time"></div></div>';
            container.appendChild(tempDiv);
            this.scrollToBottom();
        }

        updateStreamingMessage(content, renderImages = false) {
            const msgDiv = document.getElementById('streamingAIMessage');
            const textDiv = msgDiv?.querySelector('.message-text');
            if (!textDiv) return;
            textDiv.innerHTML = MarkdownParser.render(renderImages ? content : sanitizeStreamingContent(content));
            this.bindInlineMessageImages(msgDiv);
            this.scrollToBottom();
        }
        updateAIStatus(status) {
            const msg = document.getElementById('streamingAIMessage');
            const text = msg?.querySelector('.message-text');
            if (!msg || !text || !status) return;
            let panel = msg.querySelector('.ai-process');
            if (!panel) {
                panel = document.createElement('details'); panel.className = 'ai-process';
                panel.innerHTML = '<summary><span class="ai-process-spinner" aria-hidden="true"></span><span>正在思考</span><span class="ai-process-current">分析问题</span><span class="ai-process-chevron">›</span></summary><div class="ai-process-steps"></div>';
                text.parentNode.insertBefore(panel, text);
            }
            const current = panel.querySelector('.ai-process-current');
            const steps = panel.querySelector('.ai-process-steps');
            if (status.stage === 'done') { panel.open = false; panel.classList.add('is-complete'); const spinner = panel.querySelector('.ai-process-spinner'); if (spinner) spinner.outerHTML = '<span class="ai-process-icon">◇</span>'; return; }
            if (current) current.textContent = status.message || '正在处理';
            const labels = { analyzing: '分析问题', retrieving: '检索维修资料', reranking: '筛选相关案例', generating: '生成回答' };
            if (steps && status.stage) {
                let step = steps.querySelector(`[data-stage="${status.stage}"]`);
                if (!step) { step = document.createElement('div'); step.className = 'ai-process-step'; step.dataset.stage = status.stage; steps.appendChild(step); }
                step.className = `ai-process-step ${status.status === 'running' ? 'is-active' : 'is-complete'}`;
                step.innerHTML = `<span class="ai-process-step-mark">${status.status === 'complete' ? '✓' : '•'}</span><span><b>${labels[status.stage] || status.message || '处理中'}</b>${status.detail ? `<small>${status.detail}</small>` : ''}</span>`;
            }
            text.textContent = '';
        }

        updateReferenceDocuments(docs) {
            const msgDiv = document.getElementById('streamingAIMessage');
            if (msgDiv) this.loadAndDisplayDocuments(msgDiv, docs);
        }
        removeStreamingMessage() {
            document.getElementById('streamingAIMessage')?.remove();
        }

        addTemporaryUserMessage(text, imageUrls) {
            const container = document.getElementById('conversationContent');
            if (!container) return;
            const tempMessage = {
                id: 'temp',
                role: 1,
                content_text: text,
                user_uploaded_images: imageUrls.join(', '),
                created_time: new Date()
            };
            const element = this.createMessageElement(tempMessage);
            element.id = 'tempUserMessage';
            container.appendChild(element);
            this.scrollToBottom();
        }

        removeTemporaryElements() {
            document.getElementById('tempUserMessage')?.remove();
        }

        handleFileUpload(files) {
            const previewContainer = document.getElementById('attachmentPreviewContainer');
            if (!previewContainer) return;
            Array.from(files || []).forEach((file) => {
                if (!file.type.startsWith('image/')) {
                    MobileUtils.showMessage('鍙兘涓婁紶鍥剧墖鏂囦欢', 'warning');
                    return;
                }
                if (file.size > 10 * 1024 * 1024) {
                    MobileUtils.showMessage('鍥剧墖澶у皬涓嶈兘瓒呰繃10MB', 'warning');
                    return;
                }
                this.currentAttachments.push(file);
                this.createAttachmentPreview(file, previewContainer);
            });
        }

        createAttachmentPreview(file, previewContainer) {
            const index = this.currentAttachments.length - 1;
            const reader = new FileReader();
            reader.onload = (event) => {
                const preview = document.createElement('div');
                preview.className = 'attachment-preview';
                preview.dataset.index = String(index);
                preview.innerHTML = `
                    <div class="attachment-preview-content">
                        <img src="${event.target.result}" alt="棰勮">
                        <div class="attachment-info">
                            <div class="attachment-name" title="${this.escapeAttribute(file.name)}">${this.escapeHtml(file.name)}</div>
                            <div class="attachment-size">${this.formatFileSize(file.size)}</div>
                        </div>
                        <button type="button" class="btn-remove-attachment" aria-label="绉婚櫎闄勪欢"><i class="fas fa-times"></i></button>
                    </div>`;
                preview.querySelector('.btn-remove-attachment')?.addEventListener('click', () => {
                    const currentIndex = Number(preview.dataset.index);
                    this.currentAttachments.splice(currentIndex, 1);
                    preview.remove();
                    this.reindexAttachmentPreviews(previewContainer);
                });
                previewContainer.appendChild(preview);
            };
            reader.readAsDataURL(file);
        }

        reindexAttachmentPreviews(previewContainer) {
            previewContainer.querySelectorAll('.attachment-preview').forEach((item, index) => {
                item.dataset.index = String(index);
            });
        }

        clearInputAndAttachments() {
            const input = document.getElementById('messageInput');
            const preview = document.getElementById('attachmentPreviewContainer');
            if (input) {
                input.value = '';
                this.autoResizeInput();
            }
            if (preview) preview.innerHTML = '';
            this.currentAttachments = [];
        }

        autoResizeInput() {
            const input = document.getElementById('messageInput');
            if (!input) return;
            input.style.height = 'auto';
            input.style.height = `${Math.min(input.scrollHeight, 128)}px`;
        }

        initVoiceFeatures() {
            const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
            this.supportsSpeechRecognition = Boolean(SR);
            if (!this.supportsSpeechRecognition) {
                const btn = document.getElementById('voiceInputButton');
                if (btn) btn.disabled = true;
                this.updateVoiceStatus('褰撳墠娴忚鍣ㄤ笉鏀寔璇煶杈撳叆锛堝缓璁?Chrome/Edge锛?);
                return;
            }

            this.speechRecognition = new SR();
            this.speechRecognition.lang = 'zh-CN';
            this.speechRecognition.interimResults = true;
            this.speechRecognition.continuous = false;
            this.speechRecognition.onstart = () => {
                this.isListening = true;
                this.updateVoiceInputButtonState();
                this.updateVoiceStatus('姝ｅ湪鍚綘璇磋瘽...');
            };
            this.speechRecognition.onresult = (event) => {
                const input = document.getElementById('messageInput');
                if (!input) return;
                let interim = '';
                for (let i = event.resultIndex; i < event.results.length; i++) {
                    const text = event.results[i][0].transcript;
                    if (event.results[i].isFinal) this.speechFinalText += text;
                    else interim += text;
                }
                input.value = [this.speechBaseText, this.speechFinalText, interim].filter(Boolean).join(' ').replace(/\s+/g, ' ').trim();
                this.autoResizeInput();
            };
            this.speechRecognition.onend = () => {
                this.isListening = false;
                this.updateVoiceInputButtonState();
                this.updateVoiceStatus(this.speechFinalText ? '璇嗗埆瀹屾垚' : '璇煶杈撳叆宸插仠姝?);
            };
            this.speechRecognition.onerror = () => {
                this.isListening = false;
                this.updateVoiceInputButtonState();
                this.updateVoiceStatus('璇煶璇嗗埆澶辫触锛岃閲嶈瘯');
            };
        }

        toggleVoiceInput() {
            if (!this.supportsSpeechRecognition || !this.speechRecognition) {
                MobileUtils.showMessage('褰撳墠娴忚鍣ㄤ笉鏀寔璇煶杈撳叆', 'warning');
                return;
            }
            if (this.isListening) this.stopVoiceInput();
            else this.startVoiceInput();
        }

        startVoiceInput() {
            const input = document.getElementById('messageInput');
            this.speechBaseText = input ? input.value.trim() : '';
            this.speechFinalText = '';
            this.speechRecognition.start();
        }

        stopVoiceInput() {
            this.speechRecognition?.stop();
        }

        updateVoiceInputButtonState() {
            const btn = document.getElementById('voiceInputButton');
            if (!btn) return;
            btn.classList.toggle('is-active', this.isListening);
            btn.innerHTML = this.isListening ? '<i class="fas fa-microphone-slash"></i>' : '<i class="fas fa-microphone"></i>';
        }

        updateVoiceStatus(text) {
            const el = document.getElementById('voiceStatus');
            if (el) el.textContent = text || '';
        }

        bindInlineMessageImages(container) {
            if (!container) return;
            container.querySelectorAll('.message-text img').forEach((image) => {
                if (image.dataset.previewBound === '1') return;
                image.dataset.previewBound = '1';
                image.setAttribute('title', '鐐瑰嚮鏌ョ湅澶у浘');
                image.addEventListener('click', (event) => {
                    event.stopPropagation();
                    this.previewImage(image.getAttribute('src') || '');
                });
            });
        }

        previewImage(imageUrl) {
            if (!imageUrl) return;
            const isBase64 = imageUrl.startsWith('data:image');
            const imageSrc = isBase64 ? imageUrl : API_CONFIG.getAssetUrl(imageUrl);
            const fileName = isBase64 ? '棰勮鍥剧墖' : imageUrl.split('/').pop();
            const modal = document.createElement('div');
            modal.className = 'image-modal';
            modal.innerHTML = `<div class="image-modal-content"><button class="close-modal" type="button">&times;</button><img src="${this.escapeAttribute(imageSrc)}" alt="棰勮鍥剧墖"><div class="image-filename">${this.escapeHtml(fileName || '鍥剧墖')}</div></div>`;
            modal.querySelector('.close-modal')?.addEventListener('click', () => modal.remove());
            modal.addEventListener('click', (event) => {
                if (event.target === modal) modal.remove();
            });
            document.body.appendChild(modal);
        }

        showInputSection(show) {
            const inputSection = document.getElementById('inputSection');
            if (inputSection) inputSection.hidden = !show;
        }

        showEmptyState() {
            const container = document.getElementById('conversationContent');
            if (container) {
                container.innerHTML = `<div class="empty-state" id="emptyState"><div class="empty-icon"><i class="fas fa-robot"></i></div><h2>AI缁翠慨鍔╂墜</h2><p>鎮ㄥソ锛佹垜鏄偍鐨勭淮淇緟鍔〢I鍔╂墜锛屽彲浠ュ府鎮ㄨВ鍐宠澶囨晠闅溿€佹彁渚涚淮淇缓璁€佹煡鎵剧浉鍏虫枃妗ｃ€?/p><button class="empty-primary" id="emptyNewConversationBtn" type="button"><i class="fas fa-comments"></i> 寮€濮嬫柊瀵硅瘽</button></div>`;
                document.getElementById('emptyNewConversationBtn')?.addEventListener('click', () => this.createNewConversation());
            }
            this.showInputSection(false);
            const titleEl = document.getElementById('currentConversationTitle');
            const pageTitle = document.getElementById('mobilePageTitle');
            const dateEl = document.getElementById('currentConversationDate');
            if (titleEl) titleEl.textContent = '璇烽€夋嫨鎴栨柊寤轰竴涓璇?;
            if (pageTitle) pageTitle.textContent = 'AI杈呭姪瀵硅瘽';
            if (dateEl) dateEl.textContent = '';
        }

        scrollToBottom() {
            const container = document.getElementById('conversationContent');
            if (container) container.scrollTop = container.scrollHeight;
        }

        formatFileSize(bytes) {
            if (bytes === 0) return '0 Bytes';
            const k = 1024;
            const sizes = ['Bytes', 'KB', 'MB', 'GB'];
            const i = Math.floor(Math.log(bytes) / Math.log(k));
            return `${parseFloat((bytes / Math.pow(k, i)).toFixed(2))} ${sizes[i]}`;
        }

        escapeHtml(value) {
            const div = document.createElement('div');
            div.textContent = value === undefined || value === null ? '' : String(value);
            return div.innerHTML;
        }

        escapeAttribute(value) {
            return this.escapeHtml(value).replace(/"/g, '&quot;');
        }
    }

    document.addEventListener('DOMContentLoaded', () => {
        MarkdownParser.init();
        if (typeof conversationAPI === 'undefined' || typeof messageAPI === 'undefined') {
            MobileUtils.showMessage('绯荤粺鍒濆鍖栧け璐ワ紝璇峰埛鏂伴〉闈?, 'error');
            return;
        }
        window.MobileAIConversation = new MobileAIConversationSystem();
        window.MobileAIConversation.init();
    });
})();
