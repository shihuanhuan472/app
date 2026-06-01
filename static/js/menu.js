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

        this.ensureReviewMenuLink();

        const userManagementLink = document.querySelector('a[href="user-management.html"]');
        const myProfileLink = document.querySelector('a[href="user-profile.html"]');
        const mySubmissionsLink = document.querySelector('a[href="my-submissions.html"]');
        const reviewCenterLink = document.querySelector('a[href="document-review.html"]');
        const aiAssistLink = document.querySelector('a[href="ai-assist.html"]');

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

