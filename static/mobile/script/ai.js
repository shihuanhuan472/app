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
            return (user && (user.full_name || user.username)) || '用户';
        }
    };

    function patchApiRedirects() {
        if (typeof APIClient === 'undefined' || !APIClient.prototype) return;
        APIClient.prototype.refreshToken = async function refreshTokenForMobile() {
            const refreshToken = localStorage.getItem('refresh_token') || sessionStorage.getItem('refresh_token');
            if (!refreshToken) {
                MobileUtils.logout();
                throw new Error('没有可用的刷新令牌');
            }

            const response = await fetch(`${this.baseUrl}/auth/refresh`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ refresh_token: refreshToken })
            });
            const result = await response.json().catch(() => ({}));
            if (!response.ok || Number(result.code) !== 1) {
                MobileUtils.logout();
                throw new Error(result.msg || result.message || '登录已过期，请重新登录');
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
        output = output.replace(/^.*(?:img_url|image_url|配图路径|本文配图路径).*$(\r?\n)?/gmi, '');
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
            this.showHistoryFooter('加载中...');
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
                this.showHistoryFooter(this.hasMore ? '' : (conversations.length ? '没有更多对话了' : ''));
            } catch (error) {
                container.innerHTML = `<div class="history-error"><i class="fas fa-exclamation-triangle"></i><p>加载失败：${this.escapeHtml(error.message)}</p></div>`;
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
                container.innerHTML = '<div class="empty-history"><i class="fas fa-comments"></i><p>暂无对话</p></div>';
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
            const title = this.escapeHtml(conv.title || conv.name || '无标题对话');
            const date = conv.updated_time ? MobileUtils.formatDate(conv.updated_time, 'MM-DD HH:mm') : '';
            const active = id === this.currentConversationId ? ' active' : '';
            return `
                <div class="history-item${active}" data-id="${id}" role="listitem">
                    <div class="history-item-main">
                        <div class="history-item-title">${title}</div>
                        <div class="history-item-date">${date}</div>
                    </div>
                    <div class="history-item-actions">
                        <button class="btn-edit-title" type="button" title="修改标题" aria-label="修改标题"><i class="fas fa-edit"></i></button>
                        <button class="btn-delete-conversation" type="button" title="删除对话" aria-label="删除对话"><i class="fas fa-trash"></i></button>
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
                MobileUtils.showMessage('正在创建新对话...', 'info');
                const response = await conversationAPI.createConversation();
                const conversation = this.extractSingleConversation(response);
                if (!conversation || !conversation.id) throw new Error('创建对话失败');
                await this.loadConversation(Number(conversation.id), { keepDrawer: false });
                await this.loadHistoryList(1);
                this.clearInputAndAttachments();
                MobileUtils.showMessage('新对话已创建', 'success');
            } catch (error) {
                MobileUtils.showMessage(`创建对话失败：${error.message}`, 'error');
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
                MobileUtils.showMessage('无效的对话ID', 'error');
                return;
            }

            try {
                const response = await conversationAPI.getConversationById(conversationId);
                const conversation = this.extractSingleConversation(response);
                if (!conversation) throw new Error('对话不存在或无权限访问');

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
                MobileUtils.showMessage(`加载对话失败：${error.message}`, 'error');
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
            const title = conversation.title || conversation.name || '无标题对话';
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

            if (!messages || messages.length === 0) {
                container.innerHTML = '<div class="empty-state"><div class="empty-icon"><i class="fas fa-comments"></i></div><h2>对话开始</h2><p>这是您的新对话，请输入您的问题开始交流。</p></div>';
                return;
            }

            const fragment = document.createDocumentFragment();
            messages.forEach((message) => fragment.appendChild(this.createMessageElement(message)));
            container.appendChild(fragment);
            window.setTimeout(() => this.scrollToBottom(), 80);
        }

        createMessageElement(message) {
            const isAI = Number(message.role) === 0;
            const container = document.createElement('div');
            container.className = `message-container ${isAI ? 'ai' : 'user'}`;

            let html = `<div class="message-sender ${isAI ? 'ai' : 'user'}">${isAI ? 'AI助手' : '用户'}</div>`;
            html += `<div class="message-content ${isAI ? 'ai' : 'user'}">`;
            const content = message.content_text || message.content || '';
            html += `<div class="message-text">${this.formatMessageContent(content || '暂无内容', isAI)}</div>`;

            if (!isAI && message.user_uploaded_images && String(message.user_uploaded_images).trim()) {
                const images = String(message.user_uploaded_images).split(',').map((item) => item.trim()).filter(Boolean);
                if (images.length) {
                    html += '<div class="message-image-previews">';
                    images.forEach((imageUrl) => {
                        const fullUrl = imageUrl.startsWith('data:image') ? imageUrl : API_CONFIG.getAssetUrl(imageUrl);
                        const fileName = this.escapeHtml(imageUrl.split('/').pop() || '图片');
                        html += `<div class="image-preview-item" data-image-url="${this.escapeAttribute(imageUrl)}"><img src="${this.escapeAttribute(fullUrl)}" alt="${fileName}"></div>`;
                    });
                    html += '</div>';
                }
            }

            const messageTime = message.created_time ? MobileUtils.formatDate(message.created_time, 'HH:mm') : '';
            html += `<div class="message-time">${messageTime}</div></div>`;
            container.innerHTML = html;

            container.querySelectorAll('.image-preview-item').forEach((item) => {
                item.addEventListener('click', () => this.previewImage(item.dataset.imageUrl));
            });

            if (isAI && message.ai_reference_doc_ids && String(message.ai_reference_doc_ids).trim()) {
                this.loadAndDisplayDocuments(container, message.ai_reference_doc_ids);
            }

            return container;
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
                        // 兼容旧格式
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
                const title = this.escapeHtml(doc.title && doc.title.trim() ? doc.title : `文档 ${doc.doc_id}`);
                const libraryType = doc.library_type === 'knowledge' ? 'knowledge' : 'breakdown';
                const scoreHtml = typeof doc.score === 'number' && !Number.isNaN(doc.score)
                    ? `<span class="doc-score ${this.getReferenceScoreLevel(doc.score).className}">匹配度 ${(Math.max(0, Math.min(1, doc.score)) * 100).toFixed(1)}%</span>`
                    : '';
                return `<a href="../document-detail.html?id=${doc.doc_id}&source=${encodeURIComponent(libraryType)}" target="_blank" class="document-item" data-doc-id="${doc.doc_id}" data-library-type="${libraryType}"><div class="document-item-title"><i class="fas fa-external-link-alt"></i><span class="doc-title">${title}</span>${scoreHtml}</div></a>`;
            }).join('');
            return `<div class="message-documents"><div class="documents-title"><i class="fas fa-book-open"></i> 相关参考文档</div><div class="documents-list">${items}</div></div>`;
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
            const newTitle = window.prompt('请输入新的对话标题', currentTitle || '');
            if (!newTitle || !newTitle.trim() || newTitle.trim() === currentTitle) return;
            try {
                await conversationAPI.updateTitle(conversationId, newTitle.trim());
                if (this.currentConversationId === conversationId) {
                    this.updateConversationHeader({ id: conversationId, title: newTitle.trim(), updated_time: new Date() });
                }
                await this.loadHistoryList(1);
                MobileUtils.showMessage('标题已更新', 'success');
            } catch (error) {
                MobileUtils.showMessage(`更新标题失败：${error.message}`, 'error');
            }
        }

        async deleteConversation(conversationId) {
            if (!window.confirm('确定要删除这个对话吗？此操作不可恢复。')) return;
            try {
                await conversationAPI.deleteConversation(conversationId);
                if (this.currentConversationId === conversationId) {
                    this.currentConversationId = null;
                    sessionStorage.removeItem('last_conversation_id');
                    this.showEmptyState();
                }
                await this.loadHistoryList(1);
                MobileUtils.showMessage('对话已删除', 'success');
            } catch (error) {
                MobileUtils.showMessage(`删除对话失败：${error.message}`, 'error');
            }
        }

        async sendMessage() {
            if (this.isListening) this.stopVoiceInput();
            const input = document.getElementById('messageInput');
            const sendButton = document.getElementById('sendButton');
            const messageText = input ? input.value.trim() : '';

            if (!messageText && this.currentAttachments.length === 0) {
                MobileUtils.showMessage('请输入消息或上传图片', 'warning');
                return;
            }
            if (!this.currentConversationId) {
                MobileUtils.showMessage('请先选择或创建一个对话', 'warning');
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
                        this.removeTemporaryElements();
                        this.removeStreamingMessage();
                        await this.loadConversation(this.currentConversationId, { keepDrawer: true });
                    },
                    (error) => {
                        this.removeStreamingMessage();
                        this.removeTemporaryElements();
                        MobileUtils.showMessage(`发送消息失败：${error.message}`, 'error');
                    }
                );
            } catch (error) {
                MobileUtils.showMessage(`发送消息失败：${error.message}`, 'error');
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
            tempDiv.innerHTML = '<div class="message-sender ai">AI助手</div><div class="message-content ai"><div class="message-text streaming-text">正在思考...</div><div class="message-time"></div></div>';
            container.appendChild(tempDiv);
            this.scrollToBottom();
        }

        updateStreamingMessage(content, renderImages = false) {
            const msgDiv = document.getElementById('streamingAIMessage');
            const textDiv = msgDiv?.querySelector('.message-text');
            if (!textDiv) return;
            textDiv.innerHTML = MarkdownParser.render(renderImages ? content : sanitizeStreamingContent(content));
            this.scrollToBottom();
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
                    MobileUtils.showMessage('只能上传图片文件', 'warning');
                    return;
                }
                if (file.size > 10 * 1024 * 1024) {
                    MobileUtils.showMessage('图片大小不能超过10MB', 'warning');
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
                        <img src="${event.target.result}" alt="预览">
                        <div class="attachment-info">
                            <div class="attachment-name" title="${this.escapeAttribute(file.name)}">${this.escapeHtml(file.name)}</div>
                            <div class="attachment-size">${this.formatFileSize(file.size)}</div>
                        </div>
                        <button type="button" class="btn-remove-attachment" aria-label="移除附件"><i class="fas fa-times"></i></button>
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
                this.updateVoiceStatus('当前浏览器不支持语音输入（建议 Chrome/Edge）');
                return;
            }

            this.speechRecognition = new SR();
            this.speechRecognition.lang = 'zh-CN';
            this.speechRecognition.interimResults = true;
            this.speechRecognition.continuous = false;
            this.speechRecognition.onstart = () => {
                this.isListening = true;
                this.updateVoiceInputButtonState();
                this.updateVoiceStatus('正在听你说话...');
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
                this.updateVoiceStatus(this.speechFinalText ? '识别完成' : '语音输入已停止');
            };
            this.speechRecognition.onerror = () => {
                this.isListening = false;
                this.updateVoiceInputButtonState();
                this.updateVoiceStatus('语音识别失败，请重试');
            };
        }

        toggleVoiceInput() {
            if (!this.supportsSpeechRecognition || !this.speechRecognition) {
                MobileUtils.showMessage('当前浏览器不支持语音输入', 'warning');
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

        previewImage(imageUrl) {
            if (!imageUrl) return;
            const isBase64 = imageUrl.startsWith('data:image');
            const imageSrc = isBase64 ? imageUrl : API_CONFIG.getAssetUrl(imageUrl);
            const fileName = isBase64 ? '预览图片' : imageUrl.split('/').pop();
            const modal = document.createElement('div');
            modal.className = 'image-modal';
            modal.innerHTML = `<div class="image-modal-content"><button class="close-modal" type="button">&times;</button><img src="${this.escapeAttribute(imageSrc)}" alt="预览图片"><div class="image-filename">${this.escapeHtml(fileName || '图片')}</div></div>`;
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
                container.innerHTML = `<div class="empty-state" id="emptyState"><div class="empty-icon"><i class="fas fa-robot"></i></div><h2>AI维修助手</h2><p>您好！我是您的维修辅助AI助手，可以帮您解决设备故障、提供维修建议、查找相关文档。</p><button class="empty-primary" id="emptyNewConversationBtn" type="button"><i class="fas fa-comments"></i> 开始新对话</button></div>`;
                document.getElementById('emptyNewConversationBtn')?.addEventListener('click', () => this.createNewConversation());
            }
            this.showInputSection(false);
            const titleEl = document.getElementById('currentConversationTitle');
            const pageTitle = document.getElementById('mobilePageTitle');
            const dateEl = document.getElementById('currentConversationDate');
            if (titleEl) titleEl.textContent = '请选择或新建一个对话';
            if (pageTitle) pageTitle.textContent = 'AI辅助对话';
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
            MobileUtils.showMessage('系统初始化失败，请刷新页面', 'error');
            return;
        }
        window.MobileAIConversation = new MobileAIConversationSystem();
        window.MobileAIConversation.init();
    });
})();
