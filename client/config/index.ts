import { defineConfig } from '@tarojs/cli'
import { existsSync, realpathSync, statSync } from 'node:fs'
import { isAbsolute, relative, resolve } from 'node:path'
import { version } from '../package.json'

const useCI = process.argv.some(arg => arg === '--upload' || arg === '--preview')
const clientRoot = resolve(__dirname, '..')
// Operator-only staging override avoids replacing an already served H5 build during verification.
const outputRoot = process.env.YOUJU_CLIENT_OUTPUT_ROOT || (process.env.TARO_ENV === 'h5' ? 'dist/h5' : 'dist/weapp')
if (useCI && existsSync(resolve(clientRoot, '.env.local'))) {
  // CI-only settings stay in the Node process, never in the frontend env map.
  require('node:process').loadEnvFile(resolve(clientRoot, '.env.local'))
}
let privateKeyPath = process.env.MINIPROGRAM_PRIVATE_KEY_PATH || ''
if (useCI) {
  if (process.env.TARO_ENV !== 'weapp') throw new Error('Code upload/preview requires the weapp target.')
  if (!/^wx[0-9a-f]{16}$/.test(process.env.TARO_APP_ID || '') || !privateKeyPath) {
    throw new Error('Configure a real TARO_APP_ID and MINIPROGRAM_PRIVATE_KEY_PATH before uploading.')
  }
  privateKeyPath = realpathSync(resolve(clientRoot, privateKeyPath))
  if (!statSync(privateKeyPath).isFile()) throw new Error('The upload key must be a regular file.')
  for (const directory of ['src', 'dist', outputRoot]) {
    const fromBundle = relative(resolve(clientRoot, directory), privateKeyPath)
    if (fromBundle !== '..' && !fromBundle.startsWith('../') && !fromBundle.startsWith('..\\') && !isAbsolute(fromBundle)) {
      throw new Error('Keep the upload key outside client/src and client/dist.')
    }
  }
}

export default defineConfig<'webpack5'>({
  projectName: 'xdog-youju',
  date: '2026-10-10',
  designWidth: 375,
  deviceRatio: { 375: 2, 640: 1.17, 750: 1, 828: 0.905 },
  sourceRoot: 'src',
  outputRoot,
  copy: { patterns: [{ from: 'src/assets', to: `${outputRoot}/assets` }], options: {} },
  framework: 'react',
  compiler: 'webpack5',
  cache: { enable: false },
  plugins: useCI ? [[
    '@tarojs/plugin-mini-ci',
    {
      weapp: {
        appid: process.env.TARO_APP_ID,
        privateKeyPath,
        ignores: ['**/*.map', '**/.env*', '**/*.pem', '**/*.key', '**/.secrets/**'],
      },
      version: process.env.MINIPROGRAM_VERSION || version,
      desc: process.env.MINIPROGRAM_DESCRIPTION || 'Youju authentication and source administration',
    },
  ]] : [],
  env: {
    TARO_APP_API_BASE_URL: JSON.stringify(process.env.TARO_APP_API_BASE_URL || ''),
  },
  mini: {
    postcss: { pxtransform: { enable: true }, cssModules: { enable: false } },
  },
  h5: {
    htmlPluginOption: { title: '职有据' },
    publicPath: '/',
    staticDirectory: 'static',
    router: { mode: 'hash' },
    devServer: {
      host: '127.0.0.1',
      port: 10089,
      proxy: [{ context: ['/api'], target: 'http://127.0.0.1:8089' }],
    },
  },
})
