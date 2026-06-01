// 模拟数据
const MockData = {
    // 用户数据
    users: [
        {
            id: 1,
            username: 'zhangsan',
            name: '张三',
            role: 'technician',
            department: '技术部',
            phone: '13512345678',
            email: 'zhangsan@example.com',
            hireDate: '2022-03-15',
            avatarColor: '#4a9eff'
        },
        {
            id: 2,
            username: 'lisi',
            name: '李四',
            role: 'technician',
            department: '技术部',
            phone: '13612345678',
            email: 'lisi@example.com',
            hireDate: '2021-08-10',
            avatarColor: '#10b981'
        },
        {
            id: 3,
            username: 'wangwu',
            name: '王五',
            role: 'technician',
            department: '技术部',
            phone: '13712345678',
            email: 'wangwu@example.com',
            hireDate: '2020-11-22',
            avatarColor: '#f59e0b'
        },
        {
            id: 4,
            username: 'admin',
            name: '系统管理员',
            role: 'admin',
            department: '管理部',
            phone: '13812345678',
            email: 'admin@example.com',
            hireDate: '2019-05-06',
            avatarColor: '#ef4444'
        },
        {
            id: 5,
            username: 'zhaoliu',
            name: '赵六',
            role: 'technician',
            department: '维修部',
            phone: '13912345678',
            email: 'zhaoliu@example.com',
            hireDate: '2023-01-18',
            avatarColor: '#8b5cf6'
        },
        {
            id: 6,
            username: 'sunqi',
            name: '孙七',
            role: 'technician',
            department: '维修部',
            phone: '13112345678',
            email: 'sunqi@example.com',
            hireDate: '2022-09-30',
            avatarColor: '#ec4899'
        }
    ],

    // 文档示例数据（已替换为可读中文占位内容）
    documents: [
        {
            id: 1,
            title: '设备主轴异响故障处理指南',
            author: '张三',
            date: '2023-10-25',
            content: '本文介绍设备主轴异响的排查流程，包括轴承状态检查、润滑系统核验与动平衡校准。'
        },
        {
            id: 2,
            title: '工业机器人定位精度校准手册',
            author: '李四',
            date: '2023-10-20',
            content: '提供机器人定位偏差诊断步骤，涵盖零点标定、误差补偿和关键参数调整。'
        },
        {
            id: 3,
            title: '激光切割设备功率波动排查',
            author: '王五',
            date: '2023-10-18',
            content: '梳理功率不稳定的常见原因，包含电源检测、冷却回路检查和光学组件状态确认。'
        },
        {
            id: 4,
            title: '设备预防性维护标准作业程序',
            author: '赵六',
            date: '2023-10-15',
            content: '定义日检、周检、月检与易损件更换周期，提升设备稳定性与可用率。'
        },
        {
            id: 5,
            title: 'PLC 控制系统常见故障诊断',
            author: '孙七',
            date: '2023-10-12',
            content: '汇总 PLC 输入输出异常、程序逻辑错误与通信中断的定位和修复方法。'
        },
        {
            id: 6,
            title: '伺服电机过载保护参数设置说明',
            author: '张三',
            date: '2023-10-08',
            content: '说明过载阈值、温度保护与报警复位策略，帮助减少异常停机。'
        },
        {
            id: 7,
            title: '液压系统泄漏问题处理方案',
            author: '李四',
            date: '2023-09-28',
            content: '针对密封老化、管路松动与接头损伤等问题，提供检测与更换建议。'
        },
        {
            id: 8,
            title: '空压系统节能优化案例',
            author: '王五',
            date: '2023-09-20',
            content: '通过压力分区与负载联动优化，实现压缩空气系统能耗下降。'
        },
        {
            id: 9,
            title: '焊接机器人轨迹跟踪应用说明',
            author: '赵六',
            date: '2023-09-15',
            content: '介绍视觉引导与传感反馈在焊缝跟踪中的应用，提高焊接一致性。'
        },
        {
            id: 10,
            title: '自动化产线通信故障排查流程',
            author: '孙七',
            date: '2023-09-10',
            content: '针对总线中断、地址冲突与网关异常给出标准化排查步骤。'
        }
    ],

    // 搜索文档
    searchDocuments: function(query) {
        if (!query) return this.documents;

        const lowerQuery = query.toLowerCase();
        return this.documents.filter(doc =>
            doc.title.toLowerCase().includes(lowerQuery) ||
            doc.content.toLowerCase().includes(lowerQuery) ||
            doc.author.toLowerCase().includes(lowerQuery)
        );
    },

    // 获取所有文档
    getDocuments: function() {
        return this.documents;
    },

    // 获取所有用户
    getUsers: function() {
        return this.users;
    },

    // 添加新文档
    addDocument: function(document) {
        const newDoc = {
            id: this.documents.length + 1,
            ...document,
            date: Utils.formatDate(new Date())
        };
        this.documents.unshift(newDoc);
        return newDoc;
    },

    // 添加新用户
    addUser: function(user) {
        const newUser = {
            id: this.users.length + 1,
            ...user,
            hireDate: Utils.formatDate(new Date())
        };
        this.users.push(newUser);
        return newUser;
    },

    // 删除用户
    deleteUser: function(userId) {
        const index = this.users.findIndex(user => user.id === userId);
        if (index !== -1) {
            this.users.splice(index, 1);
            return true;
        }
        return false;
    }
};

// 导出到全局
window.MockData = MockData;

// js/data.js - 前端数据管理
const DataManager = {
    // 用户数据缓存
    usersCache: null,
    lastFetchTime: null,
    cacheDuration: 30000, // 30秒缓存

    // 清除缓存
    clearCache() {
        this.usersCache = null;
        this.lastFetchTime = null;
    },

    // 获取所有用户（带缓存）
    async getUsers(forceRefresh = false) {
        try {
            if (!forceRefresh &&
                this.usersCache &&
                this.lastFetchTime &&
                (Date.now() - this.lastFetchTime < this.cacheDuration)) {
                console.log('从缓存获取用户数据');
                return this.usersCache;
            }

            console.log('从 API 获取用户数据');
            const users = await adminAPI.getAllUsers();

            this.usersCache = users;
            this.lastFetchTime = Date.now();

            return users;
        } catch (error) {
            console.error('获取用户数据失败:', error);

            if (this.usersCache) {
                console.log('API 失败，使用缓存数据');
                return this.usersCache;
            }

            throw error;
        }
    },

    // 分页获取用户
    async getUsersPage(page = 1, size = 6) {
        try {
            console.log(`获取第 ${page} 页用户数据`);
            const result = await adminAPI.getUsersPage(page, size);
            return result;
        } catch (error) {
            console.error('分页获取用户失败:', error);
            throw error;
        }
    },

    // 获取单个用户
    async getUserById(id) {
        try {
            console.log(`获取用户 ${id} 详情`);

            if (this.usersCache) {
                const cachedUser = this.usersCache.find(user => user.id == id);
                if (cachedUser) {
                    console.log('从缓存获取用户详情');
                    return cachedUser;
                }
            }

            const user = await adminAPI.getUserById(id);
            return user;
        } catch (error) {
            console.error('获取用户详情失败:', error);
            throw error;
        }
    },

    // 添加用户
    async addUser(userData) {
        try {
            console.log('添加新用户:', userData);
            const result = await adminAPI.addUser(userData);
            this.clearCache();
            return result;
        } catch (error) {
            console.error('添加用户失败:', error);
            throw error;
        }
    },

    // 更新用户
    async updateUser(userData) {
        try {
            console.log('更新用户:', userData);
            const result = await adminAPI.updateUser(userData);
            this.clearCache();
            return result;
        } catch (error) {
            console.error('更新用户失败:', error);
            throw error;
        }
    },

    // 删除用户
    async deleteUser(userId) {
        try {
            console.log('删除用户:', userId);
            const result = await adminAPI.deleteUser(userId);
            this.clearCache();
            return result;
        } catch (error) {
            console.error('删除用户失败:', error);
            throw error;
        }
    },

    // 搜索用户
    async searchUsers(query) {
        try {
            const users = await this.getUsers();

            if (!query || !query.trim()) {
                return users;
            }

            const searchTerm = query.toLowerCase().trim();
            return users.filter(user => {
                return (
                    (user.full_name && user.full_name.toLowerCase().includes(searchTerm)) ||
                    (user.username && user.username.toLowerCase().includes(searchTerm)) ||
                    (user.department && user.department.toLowerCase().includes(searchTerm)) ||
                    (user.email && user.email.toLowerCase().includes(searchTerm)) ||
                    (user.phone && user.phone.includes(searchTerm))
                );
            });
        } catch (error) {
            console.error('搜索用户失败:', error);
            throw error;
        }
    }
};

// 导出到全局
window.DataManager = DataManager;
