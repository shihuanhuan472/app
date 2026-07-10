// js/menu.js - sidebar menu manager
class MenuManager {
    constructor() {
        this.currentUser = null;
        this.parseProgressTimer = null;
        this.init();
    }

    init() {
        document.addEventListener('DOMContentLoaded', () => {
            this.loadMenu();
        });
    }

    loadMenu() {
        try {
            this.currentUser = Utils.getCurrentUser();
            if (!this.currentUser) return;

            this.updateMenuByRole();
            this.setActiveMenu();
            this.initParseProgressIndicator();
        } catch (error) {
            console.error('Failed to load menu:', error);
        }
    }

    updateMenuByRole() {
        if (!this.currentUser) return;

        const isAdmin = this.isUserAdmin();
        const isTechnician = this.isUserTechnician();
        const isReviewer = this.isUserReviewer();
        const canReview = isAdmin || isReviewer;
        const canManageTags = isAdmin || isTechnician;

        this.ensureDashboardMenuLink();
        this.ensureSourceDocumentMenuLink();
        this.ensureParseProgressMenuLink();
        this.ensureReviewMenuLink();
        this.ensureTagManagementMenuLink();
        this.normalizeMenuOrder();

        const dashboardLink = document.querySelector('a[href="dashboard.html"]');
        const userManagementLink = document.querySelector('a[href="user-management.html"]');
        const myProfileLink = document.querySelector('a[href="user-profile.html"]');
        const mySubmissionsLink = document.querySelector('a[href="my-submissions.html"]');
        const reviewCenterLink = document.querySelector('a[href="document-review.html"]');
        const aiAssistLink = document.querySelector('a[href="ai-assist.html"]');
        const tagManagementLink = document.querySelector('a[href="tag-management.html"]');
        const parseProgressLink = document.querySelector('a[data-parse-progress-link="true"]');

        if (dashboardLink) {
            dashboardLink.style.display = isAdmin ? 'flex' : 'none';
        }

        if (userManagementLink) {
            userManagementLink.style.display = isAdmin ? 'flex' : 'none';
        }

        if (myProfileLink) {
            myProfileLink.style.display = 'flex';
        }

        if (aiAssistLink) {
            aiAssistLink.style.display = 'flex';
        }

        // "My Submissions" is only for technicians
        if (mySubmissionsLink) {
            mySubmissionsLink.style.display = isTechnician ? 'flex' : 'none';
        }

        // "Document Review" is only for reviewer/admin
        if (reviewCenterLink) {
            reviewCenterLink.style.display = canReview ? 'flex' : 'none';
        }

        if (tagManagementLink) {
            tagManagementLink.style.display = canManageTags ? 'flex' : 'none';
        }

        if (parseProgressLink) {
            parseProgressLink.style.display = 'none';
        }
    }

    isUserAdmin() {
        const user = this.currentUser;
        if (!user) return false;
        return Utils.hasRole(user, Utils.ROLE.ADMIN);
    }

    isUserTechnician() {
        const user = this.currentUser;
        if (!user) return false;
        return Utils.hasRole(user, Utils.ROLE.TECHNICIAN);
    }

    isUserReviewer() {
        const user = this.currentUser;
        if (!user) return false;
        return Utils.hasRole(user, Utils.ROLE.REVIEWER);
    }

    ensureDashboardMenuLink() {
        const navMenu = document.querySelector('.nav-menu');
        if (!navMenu) return;

        const exists = navMenu.querySelector('a[href="dashboard.html"]');
        if (exists) return;

        const dashboardLink = document.createElement('a');
        dashboardLink.href = 'dashboard.html';
        dashboardLink.className = 'nav-item';
        dashboardLink.style.display = 'none';
        dashboardLink.innerHTML = `
            <span class="nav-icon"><i class="fas fa-chart-line"></i></span>
            <span>数据看板</span>
        `;

        const knowledgeBaseLink = navMenu.querySelector('a[href="main.html"]');
        if (knowledgeBaseLink) {
            navMenu.insertBefore(dashboardLink, knowledgeBaseLink);
        } else {
            navMenu.insertBefore(dashboardLink, navMenu.firstChild);
        }
    }

    ensureReviewMenuLink() {
        const navMenu = document.querySelector('.nav-menu');
        if (!navMenu) return;

        const exists = navMenu.querySelector('a[href="document-review.html"]');
        if (exists) return;

        const reviewLink = document.createElement('a');
        reviewLink.href = 'document-review.html';
        reviewLink.className = 'nav-item';
        reviewLink.style.display = 'none';
        reviewLink.innerHTML = `
            <span class="nav-icon"><i class="fas fa-check-circle"></i></span>
            <span>文档审核</span>
        `;

        const knowledgeBaseLink = navMenu.querySelector('a[href="main.html"]');
        if (knowledgeBaseLink) {
            const secondItem = knowledgeBaseLink.nextElementSibling;
            if (secondItem) {
                navMenu.insertBefore(reviewLink, secondItem);
            } else {
                navMenu.appendChild(reviewLink);
            }
        } else {
            navMenu.appendChild(reviewLink);
        }
    }

    ensureSourceDocumentMenuLink() {
        const navMenu = document.querySelector('.nav-menu');
        if (!navMenu) return;

        const exists = navMenu.querySelector('a[href="source-documents.html"]');
        if (exists) return;

        const sourceLink = document.createElement('a');
        sourceLink.href = 'source-documents.html';
        sourceLink.className = 'nav-item';
        sourceLink.innerHTML = `
            <span class="nav-icon"><i class="fas fa-folder-open"></i></span>
            <span>源文档库</span>
        `;

        const knowledgeBaseLink = navMenu.querySelector('a[href="main.html"]');
        if (knowledgeBaseLink && knowledgeBaseLink.nextElementSibling) {
            navMenu.insertBefore(sourceLink, knowledgeBaseLink.nextElementSibling);
        } else if (knowledgeBaseLink) {
            navMenu.appendChild(sourceLink);
        } else {
            navMenu.insertBefore(sourceLink, navMenu.firstChild);
        }
    }

    ensureParseProgressMenuLink() {
        const navMenu = document.querySelector('.nav-menu');
        if (!navMenu) return;

        const exists = navMenu.querySelector('a[data-parse-progress-link="true"]');
        if (exists) return;

        const progressLink = document.createElement('a');
        progressLink.href = 'import-documents.html?view=parse-progress';
        progressLink.className = 'nav-item parse-progress-nav-item';
        progressLink.dataset.parseProgressLink = 'true';
        progressLink.style.display = 'none';
        progressLink.innerHTML = `
            <span class="nav-icon"><i class="fas fa-spinner fa-spin"></i></span>
            <span class="parse-progress-nav-label">解析进度</span>
            <span class="parse-progress-nav-badge" data-parse-progress-badge>0%</span>
        `;

        const sourceLink = navMenu.querySelector('a[href="source-documents.html"]');
        if (sourceLink && sourceLink.nextElementSibling) {
            navMenu.insertBefore(progressLink, sourceLink.nextElementSibling);
        } else if (sourceLink) {
            navMenu.appendChild(progressLink);
        } else {
            navMenu.appendChild(progressLink);
        }
    }

    ensureTagManagementMenuLink() {
        const navMenu = document.querySelector('.nav-menu');
        if (!navMenu) return;

        const exists = navMenu.querySelector('a[href="tag-management.html"]');
        if (exists) return;

        const tagLink = document.createElement('a');
        tagLink.href = 'tag-management.html';
        tagLink.className = 'nav-item';
        tagLink.style.display = 'none';
        tagLink.innerHTML = `
            <span class="nav-icon"><i class="fas fa-tags"></i></span>
            <span>标签管理</span>
        `;

        const userManagementLink = navMenu.querySelector('a[href="user-management.html"]');
        if (userManagementLink && userManagementLink.nextElementSibling) {
            navMenu.insertBefore(tagLink, userManagementLink.nextElementSibling);
        } else if (userManagementLink) {
            navMenu.appendChild(tagLink);
        } else {
            navMenu.appendChild(tagLink);
        }
    }

    normalizeMenuOrder() {
        const navMenu = document.querySelector('.nav-menu');
        if (!navMenu) return;

        const orderedHrefs = [
            'dashboard.html',
            'main.html',
            'source-documents.html',
            'import-documents.html?view=parse-progress',
            'my-submissions.html',
            'document-review.html',
            'ai-assist.html',
            'user-management.html',
            'tag-management.html',
            'user-profile.html',
        ];

        orderedHrefs.forEach((href) => {
            const link = navMenu.querySelector(`a[href="${href}"]`);
            if (link) {
                navMenu.appendChild(link);
            }
        });
    }

    setActiveMenu() {
        const navItems = document.querySelectorAll('.nav-item');
        navItems.forEach((item) => item.classList.remove('active'));

        const currentPath = window.location.pathname;
        const fileName = currentPath.split('/').pop();
        const query = new URLSearchParams(window.location.search);
        const source = query.get('source');
        const mode = query.get('mode');

        if (fileName.includes('import-documents') && query.get('view') === 'parse-progress') {
            const progressLink = document.querySelector('a[data-parse-progress-link="true"]');
            if (progressLink) {
                progressLink.classList.add('active');
                return;
            }
        }

        if (fileName.includes('user-form')) {
            const link = document.querySelector('a[href="user-management.html"]');
            if (link) {
                link.classList.add('active');
                return;
            }
        }

        if (fileName.includes('my-submission-detail')) {
            const mySubmissionsLink = document.querySelector('a[href="my-submissions.html"]');
            if (mySubmissionsLink) {
                mySubmissionsLink.classList.add('active');
                return;
            }
        }

        if (
            fileName.includes('add-document') ||
            fileName.includes('document-detail') ||
            fileName.includes('edit-document') ||
            fileName.includes('import-documents')
        ) {
            if (source === 'review' || source === 'my-submissions' || mode === 'review') {
                const reviewLink = document.querySelector('a[href="document-review.html"]');
                if (reviewLink) {
                    reviewLink.classList.add('active');
                    return;
                }
            }

            if (source === 'knowledge' || source === 'main') {
                const knowledgeLink = document.querySelector('a[href="main.html"]');
                if (knowledgeLink) {
                    knowledgeLink.classList.add('active');
                    return;
                }
            }

            const link = document.querySelector('a[href="main.html"]');
            if (link) {
                link.classList.add('active');
                return;
            }
        }

        const currentLink = document.querySelector(`a[href="${fileName}"]`);
        if (currentLink) {
            currentLink.classList.add('active');
        }
    }

    getAuthHeaders() {
        const headers = {
            'Content-Type': 'application/json',
            'Accept': 'application/json',
        };
        const token = localStorage.getItem('token');
        if (token) headers.Authorization = `Bearer ${token}`;
        return headers;
    }

    getApiBaseUrl() {
        if (typeof API_CONFIG === 'undefined' || !API_CONFIG.BASE_URL) return '';
        return API_CONFIG.BASE_URL.replace(/\/$/, '');
    }

    async fetchParseTaskById(taskId) {
        const baseUrl = this.getApiBaseUrl();
        if (!baseUrl || !taskId) return null;

        const response = await fetch(`${baseUrl}/document/parse_tasks/${encodeURIComponent(taskId)}`, {
            method: 'GET',
            headers: this.getAuthHeaders(),
        });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);

        const result = await response.json();
        if (result && result.code === 1) return result.data;
        throw new Error((result && result.msg) || '获取解析任务失败');
    }

    async fetchActiveParseTask() {
        const baseUrl = this.getApiBaseUrl();
        if (!baseUrl) return null;

        const response = await fetch(`${baseUrl}/document/parse_tasks/active`, {
            method: 'GET',
            headers: this.getAuthHeaders(),
        });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);

        const result = await response.json();
        if (result && result.code === 1) return result.data;
        throw new Error((result && result.msg) || '获取解析任务失败');
    }

    isParseTaskActive(task) {
        return task && (task.status === 'pending' || task.status === 'running');
    }

    isParseTaskFinished(task) {
        return task && task.status === 'finished';
    }

    calculateParseTaskProgress(task) {
        const total = Number(task && task.total_count) || 0;
        const success = Number(task && task.success_count) || 0;
        const failed = Number(task && task.failed_count) || 0;
        const finished = success + failed;
        const percent = total > 0 ? Math.min(100, Math.round((finished / total) * 100)) : 0;
        return { total, finished, percent };
    }

    updateParseProgressLink(task) {
        const link = document.querySelector('a[data-parse-progress-link="true"]');
        if (!link) return;

        if (!this.isParseTaskActive(task) && !this.isParseTaskFinished(task)) {
            link.style.display = 'none';
            return;
        }

        const progress = this.calculateParseTaskProgress(task);
        const badge = link.querySelector('[data-parse-progress-badge]');
        const label = link.querySelector('.parse-progress-nav-label');
        const icon = link.querySelector('.nav-icon i');
        const isFinished = this.isParseTaskFinished(task);

        link.style.display = 'flex';
        link.href = `import-documents.html?view=parse-progress&task_id=${encodeURIComponent(task.id)}`;
        link.title = isFinished
            ? `解析完成：${progress.finished}/${progress.total}`
            : (task.current_file_name ? `正在解析：${task.current_file_name}` : `解析进度：${progress.finished}/${progress.total}`);

        if (icon) {
            icon.className = isFinished ? 'fas fa-check-circle' : 'fas fa-spinner fa-spin';
        }

        if (badge) {
            badge.textContent = isFinished
                ? '完成'
                : progress.total > 0
                ? `${progress.finished}/${progress.total}`
                : `${progress.percent}%`;
        }
        if (label) {
            label.textContent = isFinished ? '解析完成' : '解析进度';
        }
    }

    async refreshParseProgressIndicator() {
        try {
            let task = null;
            const localTaskId = localStorage.getItem('current_parse_task_id');
            if (localTaskId) {
                task = await this.fetchParseTaskById(localTaskId);
                if (!this.isParseTaskActive(task) && !this.isParseTaskFinished(task)) {
                    task = null;
                }
            }

            if (!task) {
                task = await this.fetchActiveParseTask();
                if (this.isParseTaskActive(task)) {
                    localStorage.setItem('current_parse_task_id', String(task.id));
                }
            }

            this.updateParseProgressLink(task);
        } catch (error) {
            console.warn('刷新解析进度入口失败:', error);
            this.updateParseProgressLink(null);
        }
    }

    initParseProgressIndicator() {
        this.ensureParseProgressMenuLink();
        if (this.parseProgressTimer) {
            clearInterval(this.parseProgressTimer);
            this.parseProgressTimer = null;
        }

        this.refreshParseProgressIndicator();
        this.parseProgressTimer = setInterval(() => {
            this.refreshParseProgressIndicator();
        }, 5000);
    }
}

window.MenuManager = new MenuManager();
