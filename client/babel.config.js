module.exports = {
  presets: [['taro', {
    framework: 'react', ts: true, compiler: 'webpack5',
    ...(process.env.TARO_ENV === 'weapp'
      ? { targets: { ios: '9', android: '5' }, ignoreBrowserslistConfig: true }
      : {}),
  }]],
}
