import { motion } from 'framer-motion';
import { Download, ChevronRight, Activity, Terminal } from 'lucide-react';

/**
 * Hero 组件：Landing Page 的核心展示区域
 * 包含了极具张力的标题、描述、CTA 按钮以及视觉元素
 */
export const Hero = () => {
  return (
    <section className="relative min-h-[90vh] flex items-center pt-20 overflow-hidden px-4">
      {/* 背景装饰 - 赛博光晕 */}
      <div className="absolute top-0 left-1/4 w-96 h-96 bg-cyber-blue/20 blur-[120px] rounded-full -z-10 animate-pulse-glow" />
      <div className="absolute bottom-1/4 right-1/4 w-[500px] h-[500px] bg-cyber-purple/10 blur-[150px] rounded-full -z-10" />

      <div className="max-w-7xl mx-auto grid grid-cols-1 lg:grid-cols-2 gap-12 items-center">
        {/* 左侧文字内容 */}
        <motion.div
          initial={{ opacity: 0, x: -50 }}
          animate={{ opacity: 1, x: 0 }}
          transition={{ duration: 0.8 }}
        >
          <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-cyber-blue/10 border border-cyber-blue/30 text-cyber-blue text-sm font-mono mb-6">
            <Terminal size={14} />
            <span>VERSION 2.4.0 STABLE</span>
          </div>

          <h1 className="text-5xl md:text-7xl font-black leading-tight mb-6">
            重塑 <span className="text-transparent bg-clip-text bg-linear-to-r from-cyber-blue to-cyber-purple">贴吧自动化</span><br />
            的终极形态
          </h1>

          <p className="text-xl text-gray-400 mb-10 max-w-lg leading-relaxed">
            贴吧机甲 (TiebaMecha) 是专为高级玩家打造的赛博工具。集成 AI 语义分析、拟人化行为引擎与分布式账号管理，助力你在千变万化的规则中游刃有余。
          </p>

          <div className="flex flex-wrap gap-4">
            <button className="cyber-button flex items-center gap-2 group">
              立即下载
              <Download size={18} className="group-hover:translate-y-0.5 transition-transform" />
            </button>
            <button className="px-6 py-2 rounded-full border border-white/10 hover:bg-white/5 transition-colors flex items-center gap-2 font-bold">
              查看文档 <ChevronRight size={18} />
            </button>
          </div>

          <div className="mt-12 flex items-center gap-8 border-t border-white/5 pt-8">
            <div className="flex flex-col">
              <span className="text-2xl font-bold">12k+</span>
              <span className="text-xs text-gray-500 uppercase font-mono">活跃实例</span>
            </div>
            <div className="h-10 w-px bg-white/10" />
            <div className="flex flex-col">
              <span className="text-2xl font-bold flex items-center gap-2">
                99.9% <Activity size={16} className="text-green-500" />
              </span>
              <span className="text-xs text-gray-500 uppercase font-mono">成功率</span>
            </div>
            <div className="h-10 w-px bg-white/10" />
            <div className="flex flex-col">
              <span className="text-2xl font-bold">24/7</span>
              <span className="text-xs text-gray-500 uppercase font-mono">后台守护</span>
            </div>
          </div>
        </motion.div>

        {/* 右侧视觉形象 */}
        <motion.div
          initial={{ opacity: 0, scale: 0.8, rotate: 5 }}
          animate={{ opacity: 1, scale: 1, rotate: 0 }}
          transition={{ duration: 1, ease: "easeOut" }}
          className="relative"
        >
          <div className="relative z-10 glass-card p-2 rounded-2xl border-white/20 shadow-2xl overflow-hidden group">
            <img
              src="/tieba_mecha_hero.png"
              alt="TiebaMecha Core"
              className="rounded-xl grayscale-[0.2] group-hover:grayscale-0 transition-all duration-700 w-full object-cover aspect-[4/3]"
            />
            
            {/* 拟态 UI 装饰 */}
            <div className="absolute top-4 right-4 bg-black/60 backdrop-blur-md border border-white/10 p-3 rounded-lg font-mono text-[10px] space-y-1">
              <div className="text-cyber-blue flex justify-between gap-4">
                <span>&gt; INITIALIZING_MECHA...</span>
                <span>[DONE]</span>
              </div>
              <div className="text-gray-400 flex justify-between gap-4">
                <span>&gt; PROXY_TUNNEL: ENCRYPTED</span>
                <span>[OK]</span>
              </div>
              <div className="text-cyber-purple flex justify-between gap-4">
                <span>&gt; THREAD_STATUS: ACTIVE</span>
                <span>102ms</span>
              </div>
            </div>
          </div>

          {/* 装饰环 */}
          <div className="absolute -top-10 -right-10 w-40 h-40 border-2 border-cyber-blue/20 rounded-full -z-10 animate-pulse" />
          <div className="absolute -bottom-10 -left-10 w-24 h-24 bg-cyber-blue/10 rounded-full -z-10 blur-xl" />
        </motion.div>
      </div>
    </section>
  );
};
