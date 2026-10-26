import { Text, View } from '@tarojs/components'
import Taro from '@tarojs/taro'
import { ActionButton, Card, EmptyState, SectionLabel } from '../../components/ui'

export default function Home() {
  return <View className='screen screen--tab'>
    <Card className='principles'><View className='principle'><View className='small-line-icon line-document' /><Text>官方出处</Text></View>
      <View className='principle'><View className='small-line-icon line-check' /><Text>条件有据</Text></View>
      <View className='principle'><View className='small-line-icon line-clock' /><Text>时效待核</Text></View></Card>
    <SectionLabel>岗位动态</SectionLabel>
    <Card><EmptyState label='查阅招聘岗位' detail='按关键词、学历与用工形式查找岗位，核对官方出处。'>
      <ActionButton className='button-primary' onClick={() => Taro.switchTab({ url: '/pages/jobs/index' })}>查看岗位</ActionButton>
    </EmptyState></Card>
    <Text className='footnote'>非官方平台 · 报名前请核对原文</Text>
  </View>
}
