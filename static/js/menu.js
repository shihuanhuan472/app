// js/menu.js - sidebar menu manager
class MenuManager {
    constructor() {
        this.currentUser = null;
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
        const canManageSensitiveTerms = isAdmin;

        this.ensureDashboardMenuLink();
        this.ensureSourceDocumentMenuLink();
        this.ensureReviewMenuLink();
        this.ensureTagManagementMenuLink();
        this.ensureSensitiveTermsMenuLink();
        this.normalizeMenuOrder();

        const dashboardLink = document.querySelector('a[href="dashboard.html"]');
        const userManagementLink = document.querySelector('a[href="user-management.html"]');
        const myProfileLink = document.querySelector('a[href="user-profile.html"]');
        const mySubmissionsLink = document.querySelector('a[href="my-submissions.html"]');
        const reviewCenterLink = document.querySelector('a[href="document-review.html"]');
        const aiAssistLink = document.querySelector('a[href="ai-assist.html"]');
        const tagManagementLink = document.querySelector('a[href="tag-management.html"]');
        const sensitiveTermsLink = document.querySelector('a[href="sensitive-terms.html"]');

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

        if (sensitiveTermsLink) {
            sensitiveTermsLink.style.display = canManageSensitiveTerms ? 'flex' : 'none';
        }
    }

    isUserAdmin() {
        const user = this.currentUser;
        if (!user) return false;
        return Utils.hasPermission(user, Utils.PERMISSION.ADMIN);
    }

    isUserTechnician() {
        const user = this.currentUser;
        if (!user) return false;
        return !this.isUserAdmin() && Utils.hasPermission(user, Utils.PERMISSION.READ_WRITE);
    }

    isUserReviewer() {
        const user = this.currentUser;
        if (!user) return false;
        return Utils.hasPermission(user, Utils.PERMISSION.REVIEW);
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

    ensureSensitiveTermsMenuLink() {
        const navMenu = document.querySelector('.nav-menu');
        if (!navMenu) return;

        const exists = navMenu.querySelector('a[href="sensitive-terms.html"]');
        if (exists) return;

        const sensitiveLink = document.createElement('a');
        sensitiveLink.href = 'sensitive-terms.html';
        sensitiveLink.className = 'nav-item';
        sensitiveLink.style.display = 'none';
        sensitiveLink.innerHTML = `
            <span class="nav-icon"><i class="fas fa-user-shield"></i></span>
            <span>敏感词管理</span>
        `;

        const tagManagementLink = navMenu.querySelector('a[href="tag-management.html"]');
        if (tagManagementLink) {
            navMenu.insertBefore(sensitiveLink, tagManagementLink);
        } else {
            const userManagementLink = navMenu.querySelector('a[href="user-management.html"]');
            if (userManagementLink) {
                navMenu.insertBefore(sensitiveLink, userManagementLink.nextElementSibling);
            } else {
                navMenu.appendChild(sensitiveLink);
            }
        }
    }

    normalizeMenuOrder() {
        const navMenu = document.querySelector('.nav-menu');
        if (!navMenu) return;

        const orderedHrefs = [
            'dashboard.html',
            'main.html',
            'source-documents.html',
            'my-submissions.html',
            'document-review.html',
            'ai-assist.html',
            'user-management.html',
            'sensitive-terms.html',
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
}

window.MenuManager = new MenuManager();
