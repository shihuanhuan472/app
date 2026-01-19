// js/menu.js - 侧边栏菜单管理
class MenuManager {
    constructor() {
        this.currentUser = null;
        this.init();
    }

    async init() {
        // 等待 DOM 加载完成
        document.addEventListener('DOMContentLoaded', () => {
            this.loadMenu();
        });
    }

    async loadMenu() {
        try {
            // 获取当前用户信息
            this.currentUser = Utils.getCurrentUser();

            if (!this.currentUser) {
                console.warn('未找到用户信息，无法加载菜单');
                return;
            }

            console.log('加载菜单，当前用户:', this.currentUser);

            // 根据用户角色更新菜单
            this.updateMenuByRole();

            // 设置当前页面激活状态
            this.setActiveMenu();

        } catch (error) {
            console.error('加载菜单失败:', error);
        }
    }

    updateMenuByRole() {
        if (!this.currentUser) return;

        console.log('更新菜单，用户身份:', this.currentUser.role, this.currentUser);

        // 判断是否是管理员
        const isAdmin = this.isUserAdmin();
        console.log('是管理员吗?', isAdmin);

        // 获取菜单项
        const userManagementLink = document.querySelector('a[href="user-management.html"]');
        const myProfileLink = document.querySelector('a[href="user-profile.html"]');

        // 更新菜单显示状态
        if (userManagementLink) {
            userManagementLink.style.display = isAdmin ? 'flex' : 'none';
        }

        if (myProfileLink) {
            myProfileLink.style.display = 'flex'; // 所有用户都可见
        }
    }

    isUserAdmin() {
        const user = this.currentUser;
        if (!user) return false;

        // 多种角色判断方式
        if (user.role === 0) return true;                      // 数字 0
        if (user.role === 'admin') return true;               // 字符串 admin
        if (user.role_id === 0) return true;                  // role_id 为 0
        if (user.role_name === '管理员') return true;         // 角色名
        if (user.permissions && user.permissions.includes('admin')) return true;

        return false;
    }

    setActiveMenu() {
        // 移除所有 active 类
        const navItems = document.querySelectorAll('.nav-item');
        navItems.forEach(item => {
            item.classList.remove('active');
        });

        // 获取当前页面文件名
        const currentPath = window.location.pathname;
        const fileName = currentPath.split('/').pop();

        console.log('当前页面:', fileName);

        // 根据当前页面设置 active 类
        const currentLink = document.querySelector(`a[href="${fileName}"]`);
        if (currentLink) {
            currentLink.classList.add('active');
            console.log('设置激活菜单:', currentLink.href);
        }
    }
}

// 创建全局菜单管理器实例
window.MenuManager = new MenuManager();