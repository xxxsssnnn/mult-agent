/* eslint-env node */
/**
 * 前端 ESLint 配置。
 *
 * 背景：package.json 的 lint 脚本（eslint . --ext ts,tsx --max-warnings 0）此前
 * 因缺少配置文件直接以 exit 2 失败。本文件补齐配置，让 `npm run lint` 成为可用门禁。
 *
 * 说明：
 * - 使用 .cjs 后缀（package.json 为 "type": "module"）。
 * - 仅启用已安装的插件（@typescript-eslint、react-hooks、react-refresh），
 *   不引入未安装的 eslint-plugin-react / import 等，避免 CI 再缺依赖。
 */
module.exports = {
  root: true,
  env: {
    browser: true,
    es2020: true,
    node: true,
  },
  extends: [
    'eslint:recommended',
    'plugin:@typescript-eslint/recommended',
    'plugin:react-hooks/recommended',
  ],
  ignorePatterns: [
    'dist',
    'coverage',
    'node_modules',
    '.eslintrc.cjs',
    'vite.config.ts',
    'vitest.config.ts',
  ],
  parser: '@typescript-eslint/parser',
  parserOptions: {
    ecmaVersion: 'latest',
    sourceType: 'module',
    ecmaFeatures: { jsx: true },
  },
  plugins: ['react-refresh'],
  rules: {
    // 类型收窄在 catch/第三方回调里常需显式 any，作为警告而非阻塞（--max-warnings 0 会拦截）
    '@typescript-eslint/no-explicit-any': 'off',
    // 允许 _ 前缀的占位参数/变量
    '@typescript-eslint/no-unused-vars': [
      'error',
      { argsIgnorePattern: '^_', varsIgnorePattern: '^_' },
    ],
    'no-unused-vars': 'off',
    'react-refresh/only-export-components': [
      'warn',
      { allowConstantExport: true },
    ],
  },
}
