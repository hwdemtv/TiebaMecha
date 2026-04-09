import { motion } from 'framer-motion';
import { Layers, ShieldCheck, Repeat, UserCheck, MessageSquare, Zap } from 'lucide-react';

const features = [
  {
    title: "矩阵发帖终端",
    description: "多号轮询、随机、加权。支持 AI 智能附魔内容，让每条信息在算法眼中都是原创。",
    icon: <Layers className="text-cyber-blue" />,
    tag: "Matrix Post Terminal"
  },
  {
    title: "拟人化风控引擎",
    description: "高斯分布延迟、生理节律权重。拒绝机械化操作，极致模拟真人操作规律。",
    icon: <ShieldCheck className="text-cyber-purple" />,
    tag: "BionicDelay™"
  },
  {
    title: "智能自动回帖",
    description: "热度守护，后台守护进程定时巡检，自动维护帖子处于首页位置。",
    icon: <Repeat className="text-cyber-pink" />,
    tag: "Auto-Bump"
  },
  {
    title: "账号指纹隔离",
    description: "物理级别的账号隔离。动态分配 CUID 和 User-Agent，支持账号级代理绑定。",
    icon: <UserCheck className="text-cyber-blue" />,
    tag: "Fingerprint"
  },
  {
    title: "全域签到引擎",
    description: "批量签到所有关注贴吧，支持自定义延迟间隔与连续天数统计。",
    icon: <Zap className="text-yellow-400" />,
    tag: "One-Clip Sign"
  },
  {
    title: "自动化规则引擎",
    description: "关键词与正则表达式双模式。自动监控并精准处理违规内容。",
    icon: <MessageSquare className="text-green-400" />,
    tag: "Auto Rule"
  }
];

export const Features = () => {
  return (
    <section className="py-24 px-4 bg-[#050510]">
      <div className="max-w-6xl mx-auto">
        <div className="text-center mb-16">
          <h2 className="text-4xl md:text-5xl font-black mb-4">
            核心特权与技术方案
          </h2>
          <p className="text-gray-400 max-w-2xl mx-auto">
            贴吧机甲不仅是一个工具，更是你的赛博防线。基于深度反检测技术构建，专门针对现代平台的复杂风控算法。
          </p>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-8">
          {features.map((feature, index) => (
            <motion.div
              key={index}
              initial={{ opacity: 0, y: 20 }}
              whileInView={{ opacity: 1, y: 0 }}
              transition={{ delay: index * 0.1 }}
              viewport={{ once: true }}
              className="glass-card hover:border-cyber-blue/30 transition-all group"
            >
              <div className="w-12 h-12 rounded-xl bg-white/5 flex items-center justify-center mb-6 border border-white/10 group-hover:scale-110 transition-transform">
                {feature.icon}
              </div>
              <div className="text-xs font-mono text-cyber-blue mb-2 tracking-widest uppercase">
                {feature.tag}
              </div>
              <h3 className="text-xl font-bold mb-3">{feature.title}</h3>
              <p className="text-gray-400 text-sm leading-relaxed">
                {feature.description}
              </p>
            </motion.div>
          ))}
        </div>
      </div>
    </section>
  );
};
