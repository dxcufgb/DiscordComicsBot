import discord, os, requests, atoma, feedparser, datetime, logging, fileinput, asyncio, json, ssl
from pathlib import Path
from discord.ext import commands

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger('discord')
logger.setLevel(logging.DEBUG)
handler = logging.FileHandler(filename='discord.log', encoding='utf-8', mode='w')
handler.setFormatter(logging.Formatter('%(asctime)s:%(levelname)s:%(name)s: %(message)s'))
logger.addHandler(handler)

intents = discord.Intents(messages=True, guilds=True, reactions=True)
availableComics = {}
guildComics = {}

with open('.env') as f:
    for line in f:
        name = line.split("=")[0]
        value = line.split("=")[1]
        os.environ[name] = value

with open('comics.json') as f:
    availableComics = json.loads(f.read())
                
class ComicsBot(discord.Client):
    def __inti__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        
    async def on_ready(self):
        logger.debug("We have logged in as {0.user}".format(client))
        for guild in self.guilds:
            logger.debug("starting for guild: "+guild.name)
            mode = 'r'
            if not os.path.exists("% s" % guild.id+'-Comics.json'):
                mode = 'w+'
            if (mode == 'r'):
                with open("% s" % guild.id+'-Comics.json', mode) as comics:
                    guildComics[guild.id] = json.loads(comics.read())
            else:
                with open("% s" % guild.id+'-Comics.json', mode) as comics:
                    comics.write('{}')
                    guildComics[guild.id] = {}
        await self.checkForDailyUpdates()
            
    def writeAvailableComicsToGuildJsonFile(self, guildId):
        with open("% s" % guildId+'-Comics.json', 'w+') as jf:
            jf.write(json.dumps(guildComics[guildId], indent=4, sort_keys=True))
    
    def fetchComic(self, url):
        response = ''
        feed = ''
        name = ''
        link = ''
        try:
            ssl._create_default_https_context = ssl._create_unverified_context
            feed = feedparser.parse(url)
            name = feed.entries[0].title
        except:
            response = requests.get(url)
            feed = atoma.parse_atom_bytes(response.content)
            name = feed.entries[0].title.value
        link = feed.entries[0].links[0].href
        return name + " - " + link

    async def on_message(self, message):
        if message.author == client.user:
            return
        
        incomingGuildId = message.guild.id
        incomingChannelId = message.channel.id
        incomingChannelName = message.channel
        outgoingChannelId = os.getenv('COMICS-CHANNEL-'+"% s" % incomingGuildId)
        
        if message.content.startswith("!scc") and message.author.guild_permissions.administrator:
            if(outgoingChannelId != None):
                comicsChannel = self.get_channel(int(outgoingChannelId))
                await comicsChannel.send('Channel '+"% s" % comicsChannel+' is registered as comics channel for current server!')
            else:
                await message.channel.send('No channel has been set as dedicated comics channel!')
                
        if message.content.startswith("!sbc") and message.author.guild_permissions.administrator:
            if(outgoingChannelId != None):
                channel = self.get_channel(int(incomingChannelId))
                comicsChannel = self.get_channel(int(outgoingChannelId))
                await channel.send('Channel '+"% s" % comicsChannel+' is already registered as comics channel for current server!')
            else :
                envFile = open('.env', 'a+')
                envFile.write('COMICS-CHANNEL-'+"% s" % incomingGuildId+'='+"% s" % incomingChannelId+'\n')
                os.environ['COMICS-CHANNEL-'+"% s" % incomingGuildId] = "% s" % incomingChannelId
                channel = self.get_channel(int(incomingChannelId))
                self.writeAvailableComicsToGuildJsonFile(incomingGuildId)
                await channel.send('Setting up this channel ('+"% s" % incomingChannelName+') as dedicated comics channel!')

        if message.content.startswith("!dbc") and message.author.guild_permissions.administrator:
            channel = self.get_channel(int(incomingChannelId))
            if(outgoingChannelId == None):
                await channel.send('Channel doesn\'t exists in registry!')
            elif (int(incomingChannelId) != int(outgoingChannelId)): 
                await channel.send('Channel '+"% s" % incomingChannelName+' is not registered as comics channel for current server!')
                comicsChannel = self.get_channel(int(outgoingChannelId))
                await channel.send('Channel '+"% s" % comicsChannel+' is registered as comics channel for current server!')
            else :
                del os.environ['COMICS-CHANNEL-'+"% s" % incomingGuildId]
                envOutput = []
                with open('.env', 'w+') as jf:
                    for key in os.environ:
                        if key == 'TOKEN' or key.startswith('COMICS-CHANNEL'):
                            envOutput.append(key+':'+os.getenv(key))
                    jf.write(''.join(envOutput))
                await channel.send('Channel '+"% s" % incomingChannelName+' has been removed from registry!')
                await channel.send('To register a channel please type "!sbc" in the desired channel!')
        
        if (outgoingChannelId == None):
            channel = self.get_channel(int(incomingChannelId))
        else:
            channel = self.get_channel(int(outgoingChannelId))
            
        if message.content.startswith('!add-comic'):
            values = {}
            comic = {}
            innerValues = {}
            guildValues = {}
            inputMessage = message.content[13:]
            splitMessage = inputMessage.split('--')
            values[splitMessage[0].split(' ')[0]] = splitMessage[0].split(' ')[1]
            values[splitMessage[1].split(' ')[0]] = splitMessage[1].split(' ')[1]
            innerValues['URL'] = values['url']
            innerValues['LastURL'] = ''
            guildValues['LastURL'] = ''
            guildComics[incomingGuildId][values['name']] = guildValues
            if values['name'] not in availableComics:
                availableComics[values['name']] = innerValues
                with open('Comics.json', 'w+') as jf:
                    jf.write(json.dumps(availableComics, indent=4, sort_keys=True))
            self.writeAvailableComicsToGuildJsonFile(incomingGuildId)
            await channel.send('Comic "'+ values['name'] + '" added!')
                
        if message.content.startswith('!remove-comic'):
            inputMessage = message.content[14:]
            del guildComics[incomingGuildId][inputMessage]
            self.writeAvailableComicsToGuildJsonFile(incomingGuildId)
            await channel.send('Comic with key '+inputMessage+' removed')
                
        if message.content.startswith('!active-comics'):
            text = ''
            for comic in guildComics[incomingGuildId]:
                text += comic+':'+availableComics[comic]['URL']+'\n'
            await channel.send(text)
        
        if message.content.startswith('!available-comics'):
            text = ''
            for comic in availableComics:
                text += comic+':'+availableComics[comic]['URL']+'\n'
            await channel.send(text)

        if message.content.startswith('!help'):
            text = '\n==============================ADMIN ONLY==============================\n'
            text += '!sbc\n'
            text += 'Set Bot Channel: assigns bot to current channel!\n'
            text += 'All comics will be posted in this channel!\n'
            text += '\n'
            text += '!scc\n'
            text += 'Show Current Channel: Shows current channel used by the bot!\n'
            text += '\n'
            text += '!dbc\n'
            text += 'Delete Bot Channel: Deletes the assigned channel so the bot can be moved!\n'
            text += '\n'
            text += '==============================EVERYONE==============================\n'
            text += '!add-comic --name [NAME] --url [URL TO RSS OR ATOM FEED]\n'
            text += 'Add a comic with assigned name and point to URL\n'
            text += '\n'
            text += '!active-comics\n'
            text += 'List active comics name and URL for this server\n'
            text += '\n'
            text += '!remove-comic [NAME]\n'
            text += 'Remove comic with the assigned name, use "!active-comics" to list comics name and URLs\n'
            text += '\n'
            text += '!help\n'
            text += 'shows this help section!\n'
            text += '\n'
            text += '\n'
            text += 'Created by Dxcufgb - 2022'
            await channel.send(text)
                
    
    async def checkForDailyUpdates(self):
        await self.wait_until_ready()
        while not self.is_closed():
            logger.debug('Fetching comics links!')
            for comic in availableComics:
                fetchedLink = self.fetchComic(availableComics[comic]['URL'])
                if (fetchedLink != availableComics[comic]['LastURL']):
                    availableComics[comic]['LastURL'] = fetchedLink
            await self.sendToAllGuilds()
            logger.debug('Idling for 60 seconds...')
            await asyncio.sleep(60)

    async def sendToAllGuilds(self):
        for guild in self.guilds:
            logger.debug('running task for guild: '+"% s" % guild.id)
            channel = self.get_channel(int(os.getenv('COMICS-CHANNEL-'+"% s" % guild.id)))
            for co in guildComics[guild.id]:
                if (co in guildComics[guild.id] and availableComics[co]['LastURL'] != guildComics[guild.id][co]['LastURL']):
                    guildComics[guild.id][co]['LastURL'] = availableComics[co]['LastURL']
                    await channel.send(guildComics[guild.id][co]['LastURL'])
            if (guildComics != ''):
                self.writeAvailableComicsToGuildJsonFile(guild.id)
        
client = ComicsBot()
client.run(os.getenv('TOKEN'))