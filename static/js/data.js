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

    // 修改文档数据，移除category字段
    documents: [
        {
            id: 1,
            title: 'CNC加工中心主轴异响故障处理',
            author: '张三',
            date: '2023-10-25',
            content: '本文详细介绍了CNC加工中心主轴在高速旋转时产生异响的常见原因、诊断方法和解决方案，包括轴承检查、润滑系统维护、主轴动平衡校准等关键技术点。',
        },
        {
            id: 2,
            title: '工业机器人定位精度校准指南',
            author: '李四',
            date: '2023-10-20',
            content: '针对工业机器人在长时间运行后出现的定位偏差问题，提供详细的校准步骤和工具使用方法，涵盖零点标定、误差补偿、参数调整等内容。',
        },
        {
            id: 3,
            title: '激光切割机功率不稳定排查流程',
            author: '王五',
            date: '2023-10-18',
            content: '激光切割机功率波动的原因分析及排查流程，包含电源系统检查、冷却系统维护、激光管状态评估等关键步骤，确保设备稳定运行。',
        },
        {
            id: 4,
            title: '设备预防性维护标准作业程序',
            author: '赵六',
            date: '2023-10-15',
            content: '制定设备预防性维护的标准作业程序，包括日常检查、定期保养、部件更换周期等内容，有效延长设备使用寿命。',
        },
        {
            id: 5,
            title: 'PLC控制系统常见故障诊断',
            author: '孙七',
            date: '2023-10-12',
            content: 'PLC控制系统常见故障的诊断方法和处理技巧，涵盖输入输出模块检查、程序调试、通信故障排查等内容。',
        },
        {
            id: 6,
            title: '伺服电机过载保护设置指南',
            author: '张三',
            date: '2023-10-08',
            content: '伺服电机过载保护的参数设置和调试方法，包括电流限制、温度监控、故障复位等关键技术要点。',
        },
        {
            id: 7,
            title: '注塑机液压系统泄漏处理方案',
            author: '李四',
            date: '2023-09-28',
            content: '分析注塑机液压系统常见泄漏点，提供密封件更换、管道连接检查和维护保养的具体实施方案。',
        },
        {
            id: 8,
            title: '空压机系统节能改造案例',
            author: '王五',
            date: '2023-09-20',
            content: '通过对空压机系统进行节能改造，实现能耗降低30%的实际案例分析和具体实施方案。',
        },
        {
            id: 9,
            title: '焊接机器人焊缝跟踪技术应用',
            author: '赵六',
            date: '2023-09-15',
            content: '介绍焊接机器人的焊缝跟踪技术，包括激光传感、视觉识别等先进技术在自动化焊接中的应用。',
        },
        {
            id: 10,
            title: '自动化生产线通信故障排查',
            author: '孙七',
            date: '2023-09-10',
            content: '针对自动化生产线中常见的通信故障，提供系统的排查流程和解决方案，确保生产连续性。',
        }
    ],

    // 修改搜索函数，移除category搜索
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